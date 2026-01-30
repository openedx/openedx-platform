"""
Tests for the teams API at the HTTP request level.
"""

import json
import unittest
from datetime import datetime
from unittest.mock import patch
from urllib.parse import quote
from uuid import UUID

import ddt
import pytz
from dateutil import parser
from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.signals import post_save
from django.test import override_settings
from django.urls import reverse
from django.utils import translation
from elasticsearch.exceptions import ConnectionError  # lint-amnesty, pylint: disable=redefined-builtin
from rest_framework.test import APIClient, APITestCase
from search.search_engine_base import SearchEngine

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import AdminFactory, CourseEnrollmentFactory, StaffFactory, UserFactory
from common.djangoapps.util.testing import EventTestMixin
from common.test.utils import skip_signal
from lms.djangoapps.program_enrollments.tests.factories import ProgramEnrollmentFactory
from openedx.core.djangoapps.django_comment_common.models import FORUM_ROLE_COMMUNITY_TA, Role
from openedx.core.djangoapps.django_comment_common.utils import seed_permissions_roles
from openedx.core.lib.teams_config import TeamsConfig
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory  # lint-amnesty, pylint: disable=wrong-import-order
from .factories import CourseTeamFactory, LAST_ACTIVITY_AT
from ..models import CourseTeamMembership
from ..search_indexes import CourseTeam, CourseTeamIndexer, course_team_post_save_callback


@ddt.ddt
class TestDashboard(SharedModuleStoreTestCase):
    """Tests for the Teams dashboard."""
    test_password = "test"

    NUM_TOPICS = 10

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(
            teams_configuration=TeamsConfig({
                "max_team_size": 10,
                "topics": [
                    {
                        "name": f"Topic {topic_id}",
                        "id": topic_id,
                        "description": f"Description for topic {topic_id}"
                    }
                    for topic_id in range(cls.NUM_TOPICS)
                ]
            })
        )

    def setUp(self):
        """
        Set up tests
        """
        super().setUp()
        # will be assigned to self.client by default
        self.user = UserFactory.create(password=self.test_password)
        self.teams_url = reverse('teams_dashboard', args=[self.course.id])

    def test_anonymous(self):
        """Verifies that an anonymous client cannot access the team
        dashboard, and is redirected to the login page."""
        anonymous_client = APIClient()
        response = anonymous_client.get(self.teams_url)
        redirect_url = f'{settings.LOGIN_URL}?next={quote(self.teams_url)}'
        self.assertRedirects(response, redirect_url)

    def test_not_enrolled_not_staff(self):
        """ Verifies that a student who is not enrolled cannot access the team dashboard. """
        self.client.login(username=self.user.username, password=self.test_password)
        response = self.client.get(self.teams_url)
        assert 404 == response.status_code

    def test_not_enrolled_staff(self):
        """
        Verifies that a user with global access who is not enrolled in the course can access the team dashboard.
        """
        staff_user = UserFactory(is_staff=True, password=self.test_password)
        staff_client = APIClient()
        staff_client.login(username=staff_user.username, password=self.test_password)
        response = staff_client.get(self.teams_url)
        self.assertContains(response, "TeamsTabFactory", status_code=200)

    def test_enrolled_not_staff(self):
        """
        Verifies that a user without global access who is enrolled in the course can access the team dashboard.
        """
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)
        self.client.login(username=self.user.username, password=self.test_password)
        response = self.client.get(self.teams_url)
        self.assertContains(response, "TeamsTabFactory", status_code=200)

    def test_inactive_user(self):
        """
        Verifies that an inactive user can still access the dashboard.
        """
        user = UserFactory(is_staff=False, is_active=False, password=self.test_password)
        CourseEnrollmentFactory.create(user=user, course_id=self.course.id)
        self.client.login(username=user.username, password=self.test_password)
        response = self.client.get(self.teams_url)
        self.assertContains(response, "TeamsTabFactory", status_code=200)

    def test_enrolled_teams_not_enabled_no_teamsets(self):
        """
        Verifies that a user without global access who is enrolled in the course cannot access the team dashboard
        if the teams feature is not enabled.
        """
        course = CourseFactory.create()
        teams_url = reverse('teams_dashboard', args=[course.id])
        CourseEnrollmentFactory.create(user=self.user, course_id=course.id)
        self.client.login(username=self.user.username, password=self.test_password)
        response = self.client.get(teams_url)
        assert 404 == response.status_code

    def test_enrolled_teams_not_enabled(self):
        """
        Verifies that a user without global access who is enrolled in the course cannot access the team dashboard
        if the teams feature is not enabled.
        """
        course = CourseFactory.create(teams_configuration=TeamsConfig({
            "enabled": False,
            "max_team_size": 10,
            "topics": [
                {
                    "name": "Topic",
                    "id": "test-topic",
                    "description": "Description for test-topic"
                }
            ]
        }))
        teams_url = reverse('teams_dashboard', args=[course.id])
        CourseEnrollmentFactory.create(user=self.user, course_id=course.id)
        self.client.login(username=self.user.username, password=self.test_password)
        response = self.client.get(teams_url)
        assert 404 == response.status_code

    @unittest.skip("Fix this - getting unreliable query counts")
    def test_query_counts(self):
        # Enroll in the course and log in
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)
        self.client.login(username=self.user.username, password=self.test_password)

        # Check the query count on the dashboard with no teams
        with self.assertNumQueries(18):
            self.client.get(self.teams_url)

        # Create some teams
        for topic_id in range(self.NUM_TOPICS):
            team = CourseTeamFactory.create(
                name=f"Team for topic {topic_id}",
                course_id=self.course.id,
                topic_id=topic_id,
            )

        # Add the user to the last team
        team.add_user(self.user)

        # Check the query count on the dashboard again
        with self.assertNumQueries(24):
            self.client.get(self.teams_url)

    def test_bad_course_id(self):
        """
        Verifies expected behavior when course_id does not reference an existing course or is invalid.
        """
        bad_org = "badorgxxx"
        bad_team_url = self.teams_url.replace(self.course.id.org, bad_org)
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)
        self.client.login(username=self.user.username, password=self.test_password)
        response = self.client.get(bad_team_url)
        assert 404 == response.status_code

        bad_team_url = bad_team_url.replace(self.course.id.run, "invalid/course/id")
        response = self.client.get(bad_team_url)
        assert 404 == response.status_code

    def test_team_dashboard_user_course_specific_team_list(self):
        """Verifies team dashboard is accurate after user is added to a specific course team."""

        # Create a course two
        course_two = CourseFactory.create(
            teams_configuration=TeamsConfig({
                "max_team_size": 1,
                "topics": [
                    {
                        "name": "Test topic for course two",
                        "id": 1,
                        "description": "Description for test topic for course two."
                    }
                ]
            })
        )

        # Login and enroll user in both courses
        self.client.login(username=self.user.username, password=self.test_password)
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)
        CourseEnrollmentFactory.create(user=self.user, course_id=course_two.id)

        # Create teams in both courses
        course_one_team = CourseTeamFactory.create(name="Course one team", course_id=self.course.id, topic_id=1)
        course_two_team = CourseTeamFactory.create(  # pylint: disable=unused-variable
            name="Course two team", course_id=course_two.id, topic_id=1,
        )

        # Check that initially list of user teams in course one is empty
        course_one_teams_url = reverse('teams_dashboard', args=[self.course.id])
        response = self.client.get(course_one_teams_url)
        self.assertContains(response, '"teams": {"next": null, "previous": null, "count": 0')
        # Add user to a course one team
        course_one_team.add_user(self.user)

        # Check that list of user teams in course one is not empty, it is one now
        response = self.client.get(course_one_teams_url)
        self.assertContains(response, '"teams": {"next": null, "previous": null, "count": 1')
        # Check that list of user teams in course two is still empty
        course_two_teams_url = reverse('teams_dashboard', args=[course_two.id])
        response = self.client.get(course_two_teams_url)
        self.assertContains(response, '"teams": {"next": null, "previous": null, "count": 0')

    @ddt.unpack
    @ddt.data(
        (True, False, False),
        (False, True, False),
        (False, False, True),
        (True, True, True),
        (False, True, True),
    )
    def test_teamset_counts(self, has_open, has_private, has_public):
        topics = []
        if has_open:
            topics.append({
                "name": "test topic 1",
                "id": 1,
                "description": "Desc1",
                "type": "open"
            })
        if has_private:
            topics.append({
                "name": "test topic 2",
                "id": 2,
                "description": "Desc2",
                "type": "private_managed"
            })
        if has_public:
            topics.append({
                "name": "test topic 3",
                "id": 3,
                "description": "Desc3",
                "type": "public_managed"
            })

        course = CourseFactory.create(
            teams_configuration=TeamsConfig({"topics": topics})
        )
        teams_url = reverse('teams_dashboard', args=[course.id])
        CourseEnrollmentFactory.create(user=self.user, course_id=course.id)
        self.client.login(username=self.user.username, password=self.test_password)
        response = self.client.get(teams_url)

        expected_has_open = "hasOpenTopic: " + "true" if has_open else "false"
        expected_has_public = "hasPublicManagedTopic: " + "true" if has_public else "false"

        self.assertContains(response, expected_has_open)
        self.assertContains(response, expected_has_public)

    @ddt.unpack
    @ddt.data(
        (True, False, False),
        (False, True, False),
        (False, False, True),
        (True, True, True),
        (False, True, True),
    )
    def test_has_managed_topic(self, has_open, has_private, has_public):
        topics = []
        if has_open:
            topics.append({
                "name": "test topic 1",
                "id": 1,
                "description": "Desc1",
                "type": "open"
            })
        if has_private:
            topics.append({
                "name": "test topic 2",
                "id": 2,
                "description": "Desc2",
                "type": "private_managed"
            })
        if has_public:
            topics.append({
                "name": "test topic 3",
                "id": 3,
                "description": "Desc3",
                "type": "public_managed"
            })

        # Given a staff user browsing the teams tab
        course = CourseFactory.create(
            teams_configuration=TeamsConfig({"topics": topics})
        )
        teams_url = reverse('teams_dashboard', args=[course.id])

        staff_user = UserFactory(is_staff=True, password=self.test_password)
        staff_client = APIClient()
        staff_client.login(username=staff_user.username, password=self.test_password)

        # When I browse to the team tab
        response = staff_client.get(teams_url)

        # Then "hasManagedTopic" (which is used to show the "Manage" tab)
        # is shown if there are managed team-sets
        expected_has_managed = "hasManagedTopic: " + "true" if has_public or has_private else "false"
        self.assertContains(response, expected_has_managed)


class TeamAPITestCase(APITestCase, SharedModuleStoreTestCase):
    """Base class for Team API test cases."""

    test_password = 'password'

    @classmethod
    def setUpClass(cls):
        # pylint: disable=super-method-not-called
        with super().setUpClassAndTestData():
            base_topics = [{
                'id': f'topic_{i}', 'name': name,
                'description': f'Description for topic {i}.',
                'max_team_size': 3
            } for i, name in enumerate(['Sólar power', 'Wind Power', 'Nuclear Power', 'Coal Power'])]
            base_topics.append(
                {
                    'id': 'private_topic_1_id',
                    'name': 'private_topic_1_name',
                    'description': 'Description for topic private topic 1.',
                    'type': 'private_managed'
                }
            )
            base_topics.append(
                {
                    'id': 'private_topic_2_id',
                    'name': 'private_topic_2_name',
                    'description': 'Description for topic private topic 2.',
                    'type': 'private_managed'
                }
            )
            base_topics.append(
                {
                    'id': 'private_topic_no_teams',
                    'name': 'private_topic_no_teams_name',
                    'description': 'Description for topic private_topic_no_teams.',
                    'type': 'private_managed'
                }
            )
            teams_configuration_1 = TeamsConfig({
                'topics': base_topics,
                'max_team_size': 5
            })

            cls.test_course_1 = CourseFactory.create(
                org='TestX',
                course='TS101',
                display_name='Test Course',
                teams_configuration=teams_configuration_1
            )

            teams_configuration_2 = TeamsConfig({
                'topics':
                    [
                        {
                            'id': 'topic_5',
                            'name': 'Other Interests',
                            'description': 'Description for topic 5.'
                        },
                        {
                            'id': 'topic_6',
                            'name': 'Public Profiles',
                            'description': 'Description for topic 6.'
                        },
                        {
                            'id': 'Topic_6.5',
                            'name': 'Test Accessibility Topic',
                            'description': 'Description for Topic_6.5'
                        },
                    ],
                'max_team_size': 1
            })
            cls.test_course_2 = CourseFactory.create(
                org='MIT',
                course='6.002x',
                display_name='Circuits',
                teams_configuration=teams_configuration_2
            )

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.topics_count = 6
        cls.users = {
            'staff': AdminFactory.create(password=cls.test_password),
            'course_staff': StaffFactory.create(course_key=cls.test_course_1.id, password=cls.test_password),
            'admin': AdminFactory.create(password=cls.test_password)
        }
        cls.create_and_enroll_student(username='student_enrolled')
        cls.create_and_enroll_student(username='student_enrolled_inactive', is_active=False)
        cls.create_and_enroll_student(username='student_on_team_1_private_set_1', mode=CourseMode.MASTERS)
        cls.create_and_enroll_student(username='student_on_team_2_private_set_1', mode=CourseMode.MASTERS)
        cls.create_and_enroll_student(username='student_not_member_of_private_teams', mode=CourseMode.MASTERS)
        cls.create_and_enroll_student(username='student_enrolled_not_on_team')
        cls.create_and_enroll_student(username='student_unenrolled', courses=[])

        # Make this student a community TA.
        cls.create_and_enroll_student(username='community_ta')
        seed_permissions_roles(cls.test_course_1.id)
        community_ta_role = Role.objects.get(name=FORUM_ROLE_COMMUNITY_TA, course_id=cls.test_course_1.id)
        community_ta_role.users.add(cls.users['community_ta'])

        # This student is enrolled in both test courses and is a member of a team in each course, but is not on the
        # same team as student_enrolled.
        cls.create_and_enroll_student(
            courses=[cls.test_course_1, cls.test_course_2],
            username='student_enrolled_both_courses_other_team'
        )

        # Make this student have a public profile
        cls.create_and_enroll_student(
            courses=[cls.test_course_2],
            username='student_enrolled_public_profile'
        )
        profile = cls.users['student_enrolled_public_profile'].profile
        profile.year_of_birth = 1970
        profile.save()

        # This student is enrolled in the other course, but not yet a member of a team. This is to allow
        # course_2 to use a max_team_size of 1 without breaking other tests on course_1
        cls.create_and_enroll_student(
            courses=[cls.test_course_2],
            username='student_enrolled_other_course_not_on_team'
        )

        # This is a Masters student who should be in the organization protected bubble
        cls.create_and_enroll_student(
            courses=[cls.test_course_1, cls.test_course_2],
            username='student_masters',
            mode=CourseMode.MASTERS
        )
        cls.create_and_enroll_student(
            courses=[cls.test_course_1, cls.test_course_2],
            username='student_masters_not_on_team',
            mode=CourseMode.MASTERS
        )

        with skip_signal(
            post_save,
            receiver=course_team_post_save_callback,
            sender=CourseTeam,
            dispatch_uid='teams.signals.course_team_post_save_callback'
        ):
            cls.solar_team = CourseTeamFactory.create(
                name='Sólar team',
                course_id=cls.test_course_1.id,
                topic_id='topic_0'
            )
            cls.wind_team = CourseTeamFactory.create(
                name='Wind Team',
                course_id=cls.test_course_1.id,
                topic_id='topic_1'
            )
            cls.nuclear_team = CourseTeamFactory.create(
                name='Nuclear Team',
                course_id=cls.test_course_1.id,
                topic_id='topic_2'
            )
            cls.another_team = CourseTeamFactory.create(
                name='Another Team',
                course_id=cls.test_course_2.id,
                topic_id='topic_5'
            )
            cls.public_profile_team = CourseTeamFactory.create(
                name='Public Profile Team',
                course_id=cls.test_course_2.id,
                topic_id='topic_6'
            )
            cls.search_team = CourseTeamFactory.create(
                name='Search',
                description='queryable text',
                country='GS',
                language='to',
                course_id=cls.test_course_2.id,
                topic_id='topic_7'
            )
            cls.chinese_team = CourseTeamFactory.create(
                name='著文企臺個',
                description='共樣地面較，件展冷不護者這與民教過住意，國制銀產物助音是勢一友',
                country='CN',
                language='zh_HANS',
                course_id=cls.test_course_2.id,
                topic_id='topic_7'
            )

            cls.masters_only_team = CourseTeamFactory.create(
                name='masters_course_1',
                description='masters student group',
                country='US',
                language='EN',
                course_id=cls.test_course_1.id,
                topic_id='topic_0',
                organization_protected=True
            )

            cls.team_1_in_private_teamset_1 = CourseTeamFactory.create(
                name='team 1 in private teamset 1',
                description='team 1 in private teamset 1 desc',
                country='US',
                language='EN',
                course_id=cls.test_course_1.id,
                topic_id='private_topic_1_id',
                organization_protected=True
            )

            cls.team_2_in_private_teamset_1 = CourseTeamFactory.create(
                name='team 2 in private teamset 1',
                description='team 2 in private teamset 1 desc',
                country='US',
                language='EN',
                course_id=cls.test_course_1.id,
                topic_id='private_topic_1_id',
                organization_protected=True
            )

            cls.team_1_in_private_teamset_2 = CourseTeamFactory.create(
                name='team 1 in private teamset 2',
                description='team 1 in private teamset 2 desc',
                country='US',
                language='EN',
                course_id=cls.test_course_1.id,
                topic_id='private_topic_2_id',
                organization_protected=True
            )

        cls.test_team_name_id_map = {team.name: team for team in (
            cls.solar_team,
            cls.wind_team,
            cls.nuclear_team,
            cls.another_team,
            cls.public_profile_team,
            cls.search_team,
            cls.chinese_team,
            cls.masters_only_team,
            cls.team_1_in_private_teamset_1,
            cls.team_2_in_private_teamset_1,
            cls.team_1_in_private_teamset_2,
        )}

        for user, course in [('staff', cls.test_course_1), ('course_staff', cls.test_course_1)]:
            CourseEnrollment.enroll(
                cls.users[user], course.id, check_access=True
            )

        # Django Rest Framework v3 requires us to pass a request to serializers
        # that have URL fields.  Since we're invoking this code outside the context
        # of a request, we need to simulate that there's a request.
        cls.solar_team.add_user(cls.users['student_enrolled'])
        cls.nuclear_team.add_user(cls.users['student_enrolled_both_courses_other_team'])
        cls.another_team.add_user(cls.users['student_enrolled_both_courses_other_team'])
        cls.public_profile_team.add_user(cls.users['student_enrolled_public_profile'])
        cls.masters_only_team.add_user(cls.users['student_masters'])
        cls.team_1_in_private_teamset_1.add_user(cls.users['student_on_team_1_private_set_1'])
        cls.team_2_in_private_teamset_1.add_user(cls.users['student_on_team_2_private_set_1'])

    def build_membership_data_raw(self, username, team):
        """Assembles a membership creation payload based on the raw values provided."""
        return {'username': username, 'team_id': team}

    def build_membership_data(self, username, team):
        """Assembles a membership creation payload based on the username and team model provided."""
        return self.build_membership_data_raw(self.users[username].username, team.team_id)

    @classmethod
    def create_and_enroll_student(cls, is_active=True, courses=None, username=None, mode=None, external_key=None):
        """ Creates a new student and enrolls that student in the course.

        Adds the new user to the cls.users dictionary with the username as the key.

        Returns the username once the user has been created.
        """
        kwargs = {
            'password': cls.test_password,
            'is_active': is_active,
        }
        if username is not None:
            kwargs['username'] = username

        user = UserFactory.create(**kwargs)
        courses = courses if courses is not None else [cls.test_course_1]
        for course in courses:
            CourseEnrollment.enroll(user, course.id, mode=mode, check_access=True)
        cls.users[user.username] = user

        if external_key is not None:
            ProgramEnrollmentFactory(
                user=user,
                external_user_key=external_key,
                program_uuid=UUID("88888888-4444-3333-1111-000000000000"),
                curriculum_uuid=UUID("77777777-4444-2222-1111-000000000000"),
                status='enrolled'
            )

        return user.username

    def login(self, user):
        """Given a user string, logs the given user in.

        Used for testing with ddt, which does not have access to self in
        decorators. If user is 'student_inactive', then an inactive user will
        be both created and logged in.
        """
        if user == 'student_inactive':
            student_inactive = UserFactory.create(password=self.test_password)
            self.client.login(username=student_inactive.username, password=self.test_password)
            student_inactive.is_active = False
            student_inactive.save()
        else:
            if user not in self.users:
                missing_user = User.objects.get(username=user)
                self.users[missing_user.username] = missing_user

            self.client.login(username=self.users[user].username, password=self.test_password)

    def make_call(self, url, expected_status=200, method='get', data=None, content_type=None, **kwargs):
        """Makes a call to the Team API at the given url with method and data.

        If a user is specified in kwargs, that user is first logged in.
        """
        user = kwargs.pop('user', 'student_enrolled_not_on_team')
        if user:
            self.login(user)
        func = getattr(self.client, method)
        if content_type:
            response = func(url, data=data, content_type=content_type)
        else:
            response = func(url, data=data)
        assert expected_status == response.status_code, "Expected status {expected} but got {actual}: {content}" \
            .format(
                expected=expected_status,
                actual=response.status_code,
                content=response.content.decode(response.charset),
            )

        if expected_status == 200:
            return json.loads(response.content.decode('utf-8'))
        else:
            return response

    def get_teams_list(self, expected_status=200, data=None, no_course_id=False, **kwargs):
        """Gets the list of teams as the given user with data as query params. Verifies expected_status."""
        data = data if data else {}
        if 'course_id' not in data and not no_course_id:
            data.update({'course_id': str(self.test_course_1.id)})
        return self.make_call(reverse('teams_list'), expected_status, 'get', data, **kwargs)

    def build_team_data(
        self,
        name="Test team",
        course=None,
        description="Filler description",
        topic_id="topic_0",
        **kwargs
    ):
        """Creates the payload for creating a team. kwargs can be used to specify additional fields."""
        data = kwargs
        course = course if course else self.test_course_1
        data.update({
            'name': name,
            'course_id': str(course.id),
            'description': description,
            'topic_id': topic_id,
        })
        return data

    def post_create_team(self, expected_status=200, data=None, **kwargs):
        """Posts data to the team creation endpoint. Verifies expected_status."""
        # return self.make_call(reverse('teams_list'), expected_status, 'post', data, topic_id='topic_0', **kwargs)
        return self.make_call(reverse('teams_list'), expected_status, 'post', data, **kwargs)

    def get_team_detail(self, team_id, expected_status=200, data=None, **kwargs):
        """Gets detailed team information for team_id. Verifies expected_status."""
        return self.make_call(reverse('teams_detail', args=[team_id]), expected_status, 'get', data, **kwargs)

    def delete_team(self, team_id, expected_status, **kwargs):
        """Delete the given team. Verifies expected_status."""
        return self.make_call(reverse('teams_detail', args=[team_id]), expected_status, 'delete', **kwargs)

    def patch_team_detail(self, team_id, expected_status, data=None, **kwargs):
        """Patches the team with team_id using data. Verifies expected_status."""
        return self.make_call(
            reverse('teams_detail', args=[team_id]),
            expected_status,
            'patch',
            json.dumps(data) if data else None,
            'application/merge-patch+json',
            **kwargs
        )

    def get_team_assignments(self, team_id, expected_status=200, **kwargs):
        """ Get the open response assessments assigned to a team """
        return self.make_call(
            reverse('teams_assignments_list', args=[team_id]),
            expected_status,
            **kwargs
        )

    def get_topics_list(self, expected_status=200, data=None, **kwargs):
        """Gets the list of topics, passing data as query params. Verifies expected_status."""
        return self.make_call(reverse('topics_list'), expected_status, 'get', data, **kwargs)

    def get_topic_detail(self, topic_id, course_id, expected_status=200, data=None, **kwargs):
        """Gets a single topic, passing data as query params. Verifies expected_status."""
        return self.make_call(
            reverse('topics_detail', kwargs={'topic_id': topic_id, 'course_id': str(course_id)}),
            expected_status,
            'get',
            data,
            **kwargs
        )

    def get_membership_list(self, expected_status=200, data=None, **kwargs):
        """Gets the membership list, passing data as query params. Verifies expected_status."""
        return self.make_call(reverse('team_membership_list'), expected_status, 'get', data, **kwargs)

    def post_create_membership(self, expected_status=200, data=None, **kwargs):
        """Posts data to the membership creation endpoint. Verifies expected_status."""
        return self.make_call(reverse('team_membership_list'), expected_status, 'post', data, **kwargs)

    def get_membership_detail(self, team_id, username, expected_status=200, data=None, **kwargs):
        """Gets an individual membership record, passing data as query params. Verifies expected_status."""
        return self.make_call(
            reverse('team_membership_detail', args=[team_id, username]),
            expected_status,
            'get',
            data,
            **kwargs
        )

    def delete_membership(self, team_id, username, expected_status=200, **kwargs):
        """Deletes an individual membership record. Verifies expected_status."""
        url = reverse('team_membership_detail', args=[team_id, username]) + '?admin=true'
        return self.make_call(url, expected_status, 'delete', **kwargs)

    def verify_expanded_public_user(self, user):
        """Verifies that fields exist on the returned user json indicating that it is expanded."""
        for field in ['username', 'url', 'bio', 'country', 'profile_image', 'time_zone', 'language_proficiencies']:
            assert field in user

    def verify_expanded_private_user(self, user):
        """Verifies that fields exist on the returned user json indicating that it is expanded."""
        for field in ['username', 'url', 'profile_image']:
            assert field in user
        for field in ['bio', 'country', 'time_zone', 'language_proficiencies']:
            assert field not in user

    def verify_expanded_team(self, team):
        """Verifies that fields exist on the returned team json indicating that it is expanded."""
        for field in ['id', 'name', 'course_id', 'topic_id', 'date_created', 'description']:
            assert field in team

    def reset_search_index(self):
        """Clear out the search index and reindex the teams."""
        CourseTeamIndexer.engine().destroy()
        for team in self.test_team_name_id_map.values():
            CourseTeamIndexer.index(team)


@ddt.ddt
class TestListTeamsAPI(EventTestMixin, TeamAPITestCase):
    """Test cases for the team listing API endpoint."""

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp('lms.djangoapps.teams.utils.tracker')

    @ddt.data(
        (None, 401),
        ('student_inactive', 403),
        ('student_unenrolled', 403),
        ('student_enrolled', 200, 3),
        ('student_enrolled_inactive', 200, 3),
        ('staff', 200, 7),
        ('course_staff', 200, 7),
        ('community_ta', 200, 3),
        ('student_masters', 200, 1)
    )
    @ddt.unpack
    def test_access(self, user, status, expected_teams_count=0):
        teams = self.get_teams_list(user=user, expected_status=status)
        if status == 200:
            assert expected_teams_count == teams['count']

    def test_missing_course_id(self):
        self.get_teams_list(400, no_course_id=True)

    def verify_names(self, data, status, names=None, **kwargs):
        """Gets a team listing with data as query params, verifies status, and then verifies team names if specified."""
        teams = self.get_teams_list(data=data, expected_status=status, **kwargs)
        if names is not None and 200 <= status < 300:
            results = teams['results']
            assert sorted(names) == sorted([team['name'] for team in results])

    def test_filter_invalid_course_id(self):
        self.verify_names({'course_id': 'no_such_course'}, 400)

    def test_filter_course_id(self):
        self.verify_names(
            {'course_id': str(self.test_course_2.id)},
            200,
            ['Another Team', 'Public Profile Team', 'Search', '著文企臺個'],
            user='staff'
        )

    def test_filter_topic_id(self):
        self.verify_names({'course_id': str(self.test_course_1.id), 'topic_id': 'topic_0'}, 200, ['Sólar team'])

    def test_filter_username(self):
        self.verify_names({
            'course_id': str(self.test_course_1.id),
            'username': 'student_enrolled'
        }, 200, ['Sólar team'])
        self.verify_names({'course_id': str(self.test_course_1.id), 'username': 'staff'}, 200, [])

    @ddt.data(
        (None, 200, ['Nuclear Team', 'Sólar team', 'Wind Team']),
        ('name', 200, ['Nuclear Team', 'Sólar team', 'Wind Team']),
        # Note that "Nuclear Team" and "Solar team" have the same open_slots.
        # "Solar team" comes first due to secondary sort by last_activity_at.
        ('open_slots', 200, ['Wind Team', 'Sólar team', 'Nuclear Team']),
        # Note that "Wind Team" and "Nuclear Team" have the same last_activity_at.
        # "Wind Team" comes first due to secondary sort by open_slots.
        ('last_activity_at', 200, ['Sólar team', 'Wind Team', 'Nuclear Team']),
    )
    @ddt.unpack
    def test_order_by(self, field, status, names):
        # Make "Solar team" the most recently active team.
        # The CourseTeamFactory sets the last_activity_at to a fixed time (in the past), so all of the
        # other teams have the same last_activity_at.
        with skip_signal(
            post_save,
            receiver=course_team_post_save_callback,
            sender=CourseTeam,
            dispatch_uid='teams.signals.course_team_post_save_callback'
        ):
            solar_team = self.test_team_name_id_map['Sólar team']
            solar_team.last_activity_at = datetime.utcnow().replace(tzinfo=pytz.utc)
            solar_team.save()

        data = {'order_by': field} if field else {}
        self.verify_names(data, status, names)

    def test_order_by_with_text_search(self):
        data = {'order_by': 'name', 'text_search': 'search'}
        self.verify_names(data, 400, [])
        self.assert_no_events_were_emitted()

    @ddt.data((404, {'course_id': 'course-v1:no+such+course'}), (400, {'topic_id': 'no_such_topic'}))
    @ddt.unpack
    def test_no_results(self, status, data):
        self.get_teams_list(status, data)

    def test_page_size(self):
        result = self.get_teams_list(200, {'page_size': 2})
        assert 2 == result['num_pages']

    def test_non_member_trying_to_get_private_topic(self):
        """
        Verifies that when a student that is enrolled in a course, but is NOT a member of
        a private team set, asks for information about that team set, an empty list is returned.
        """
        result = self.get_teams_list(data={'topic_id': 'private_topic_1_id'})
        assert [] == result['results']

    def test_member_trying_to_get_private_topic(self):
        """
        Verifies that when a student that is enrolled in a course, and IS a member of
        a private team set, asks for information about that team set, information about the teamset is returned.
        """
        result = self.get_teams_list(data={'topic_id': 'private_topic_1_id'}, user='student_on_team_1_private_set_1')
        assert 1 == len(result['results'])
        assert 'private_topic_1_id' == result['results'][0]['topic_id']
        assert [] != result['results']

    def test_course_staff_getting_information_on_private_topic(self):
        """
        Verifies that when an admin browses to a private team set,
         information about the teams in the teamset is returned even if the admin is not in any teams.
        """
        result = self.get_teams_list(data={'topic_id': 'private_topic_1_id'}, user='course_staff')
        assert 2 == len(result['results'])

    @ddt.unpack
    @ddt.data(
        ('student_masters_not_on_team', 1),
        ('student_masters', 1),
        ('student_enrolled', 0),
        ('staff', 1),
    )
    def test_text_search_organization_protected(self, user, expected_results):
        """
        When doing a text search as different users, will the masters_only team show up?
        Only staff, or people who are within the organization_protected bubble should be
        able to see the masters team
        """
        self.reset_search_index()
        result = self.get_teams_list(
            data={'text_search': 'master'},
            user=user,
        )
        assert result['count'] == expected_results

    @ddt.unpack
    @ddt.data(
        ('student_on_team_1_private_set_1', True, False, False),
        ('student_on_team_2_private_set_1', False, True, False),
        ('student_enrolled', False, False, False),
        ('student_masters', False, False, False),
        ('staff', True, True, True),
    )
    def test_text_search_private_teamset(self, user, can_see_private_1_1, can_see_private_1_2, can_see_private_2_1):
        """
        When doing a text search as different users, will private_managed teams show up?
        Only staff should be able to see all private_managed teams.
        Students enrolled in a private_managed teams should be able to see their team, and no others.
        """
        self.reset_search_index()
        result = self.get_teams_list(
            data={'text_search': 'private'},
            user=user,
        )
        teams = {team['name'] for team in result['results']}
        expected_teams = set()
        if can_see_private_1_1:
            expected_teams.add(self.team_1_in_private_teamset_1.name)
        if can_see_private_1_2:
            expected_teams.add(self.team_2_in_private_teamset_1.name)
        if can_see_private_2_1:
            expected_teams.add(self.team_1_in_private_teamset_2.name)
        assert expected_teams == teams

    def test_page(self):
        result = self.get_teams_list(200, {'page_size': 1, 'page': 3})
        assert 3 == result['num_pages']
        assert result['next'] is None
        assert result['previous'] is not None

    def test_expand_private_user(self):
        # Use the default user which is already private because to year_of_birth is set
        result = self.get_teams_list(200, {'expand': 'user', 'topic_id': 'topic_0'})
        self.verify_expanded_private_user(result['results'][0]['membership'][0]['user'])

    def test_expand_public_user(self):
        result = self.get_teams_list(
            200,
            {
                'expand': 'user',
                'topic_id': 'topic_6',
                'course_id': str(self.test_course_2.id)
            },
            user='student_enrolled_public_profile'
        )
        self.verify_expanded_public_user(result['results'][0]['membership'][0]['user'])

    @ddt.data(
        ('search', ['Search']),
        ('queryable', ['Search']),
        ('Tonga', ['Search']),
        ('Island', ['Search']),
        ('not-a-query', []),
        ('team', ['Another Team', 'Public Profile Team']),
        ('著文企臺個', ['著文企臺個']),
    )
    @ddt.unpack
    def test_text_search(self, text_search, expected_team_names):
        self.reset_search_index()
        self.verify_names(
            {'course_id': str(self.test_course_2.id), 'text_search': text_search},
            200,
            expected_team_names,
            user='student_enrolled_public_profile'
        )

        self.assert_event_emitted(
            'edx.team.searched',
            search_text=text_search,
            topic_id=None,
            number_of_results=len(expected_team_names)
        )

        # Verify that the searches still work for a user from a different locale
        with translation.override('ar'):
            self.reset_search_index()
            self.verify_names(
                {'course_id': str(self.test_course_2.id), 'text_search': text_search},
                200,
                expected_team_names,
                user='student_enrolled_public_profile'
            )

    @ddt.data(
        ('masters', ['masters_course_1']),
        ('group', ['masters_course_1']),
        ('Sólar', []),
        ('Wind', []),
    )
    @ddt.unpack
    def test_text_search_masters(self, text_search, expected_team_names):
        # Verify that the search is working with Masters learner
        self.reset_search_index()
        self.verify_names(
            {'course_id': str(self.test_course_1.id), 'text_search': text_search},
            200,
            expected_team_names,
            user='student_masters'
        )

    def test_delete_removed_from_search(self):
        team = CourseTeamFactory.create(
            name='zoinks',
            course_id=self.test_course_1.id,
            topic_id='topic_0'
        )
        self.verify_names(
            {'course_id': str(self.test_course_1.id), 'text_search': 'zoinks'},
            200,
            [team.name],
            user='staff'
        )
        team.delete()
        self.verify_names(
            {'course_id': str(self.test_course_1.id), 'text_search': 'zoinks'},
            200,
            [],
            user='staff'
        )

    def test_duplicates_and_nontopic_private_teamsets(self):
        """
        Test for a bug where non-admin users would have their private memberships returned from this endpoint
        despite the topic, and duplicate entries for teams in the topic that was being queried (EDUCATOR-5042)
        """
        # create a team in a private teamset and add a user
        unprotected_team_in_private_teamset = CourseTeamFactory.create(
            name='unprotected_team_in_private_teamset',
            description='unprotected_team_in_private_teamset',
            course_id=self.test_course_1.id,
            topic_id='private_topic_1_id',
        )
        unprotected_team_in_private_teamset.add_user(self.users['student_enrolled'])

        # make some more users and put them in the solar team.
        another_student_username = 'another_student'
        yet_another_student_username = 'yet_another_student'
        self.create_and_enroll_student(username=another_student_username)
        self.create_and_enroll_student(username=yet_another_student_username)

        self._add_missing_user(another_student_username)
        self._add_missing_user(yet_another_student_username)

        self.solar_team.add_user(self.users[another_student_username])
        self.solar_team.add_user(self.users[yet_another_student_username])

        teams = self.get_teams_list(data={'topic_id': self.solar_team.topic_id}, user='student_enrolled')
        team_names = [team['name'] for team in teams['results']]
        team_names.sort()
        assert team_names == [self.solar_team.name]

        teams = self.get_teams_list(data={'topic_id': self.solar_team.topic_id}, user='staff')
        team_names = [team['name'] for team in teams['results']]
        team_names.sort()
        assert team_names == [self.solar_team.name, self.masters_only_team.name]

    def test_teams_list_for_user_for_specific_course(self):
        """Verifies teams list for user for specific course."""

        # Create and enroll user in both courses
        user = self.create_and_enroll_student(
            courses=[self.test_course_1, self.test_course_2],
            username='test_user_enrolled_both_courses'
        )
        course_one_data = {'course_id': str(self.test_course_1.id), 'username': user}
        course_two_data = {'course_id': str(self.test_course_2.id), 'username': user}

        # Check that initially list of user teams in course one is empty
        team_list = self.get_teams_list(user=user, expected_status=200, data=course_one_data)
        assert team_list['count'] == 0

        # Add user to a course one team
        self.solar_team.add_user(self.users[user])

        # Check that list of user teams in course one is not empty now
        team_list = self.get_teams_list(user=user, expected_status=200, data=course_one_data)
        assert team_list['count'] == 1

        # Check that list of user teams in course two is still empty
        team_list = self.get_teams_list(user=user, expected_status=200, data=course_two_data)
        assert team_list['count'] == 0

    def _add_missing_user(self, missing_user):
        """
        django32 TestCase.setUpTestData() are now isolated for each test method.
        In case of missing user adding this to list.
        """
        if missing_user not in self.users:
            missing_user = User.objects.get(username=missing_user)
            self.users[missing_user.username] = missing_user


@ddt.ddt
class TestCreateTeamAPI(EventTestMixin, TeamAPITestCase):
    """Test cases for the team creation endpoint."""

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp('lms.djangoapps.teams.utils.tracker')

    @ddt.data(
        (None, 401),
        ('student_inactive', 403),
        ('student_unenrolled', 403),
        ('student_enrolled_not_on_team', 200),
        ('student_enrolled_inactive', 200),
        ('staff', 200),
        ('course_staff', 200),
        ('community_ta', 200),
    )
    @ddt.unpack
    def test_access(self, user, status):
        team = self.post_create_team(status, self.build_team_data(name="New Team"), user=user)
        if status == 200:
            self.verify_expected_team_id(team, 'new-team')
            teams = self.get_teams_list(user=user)
            assert 'New Team' in [team['name'] for team in teams['results']]

    def _expected_team_id(self, team, expected_prefix):
        """ Return the team id that we'd expect given this team data and this prefix. """
        return expected_prefix + '-' + team['discussion_topic_id']

    def verify_expected_team_id(self, team, expected_prefix):
        """ Verifies that the team id starts with the specified prefix and ends with the discussion_topic_id """
        assert 'id' in team
        assert 'discussion_topic_id' in team
        assert team['id'] == self._expected_team_id(team, expected_prefix)

    def test_naming(self):
        new_teams = [
            self.post_create_team(data=self.build_team_data(name=name), user=self.create_and_enroll_student())
            for name in ["The Best Team", "The Best Team", "A really long team name"]
        ]
        # Check that teams with the same name have unique IDs.
        self.verify_expected_team_id(new_teams[0], 'the-best-team')
        self.verify_expected_team_id(new_teams[1], 'the-best-team')
        assert new_teams[0]['id'] != new_teams[1]['id']

        # Verify expected truncation behavior with names > 20 characters.
        self.verify_expected_team_id(new_teams[2], 'a-really-long-team-n')

    @ddt.data((400, {
        'name': 'Bad Course ID',
        'course_id': 'no_such_course',
        'description': "Filler Description"
    }), (404, {
        'name': "Non-existent course ID",
        'course_id': 'course-v1:no+such+course',
        'description': "Filler Description"
    }))
    @ddt.unpack
    def test_bad_course_data(self, status, data):
        self.post_create_team(status, data)

    def test_bad_topic_id(self):
        self.post_create_team(
            404,
            data=self.build_team_data(topic_id='asdfasdfasdfa'),
            user='staff'
        )

    def test_missing_topic_id(self):
        data = self.build_team_data()
        data.pop('topic_id')
        self.post_create_team(400, data=data, user='staff')

    def test_student_in_teamset(self):
        response = self.post_create_team(
            400,
            data=self.build_team_data(
                name="Doomed team",
                course=self.test_course_1,
                description="Overly ambitious student"
            ),
            user='student_enrolled'
        )
        assert 'You are already in a team in this teamset.' == \
               json.loads(response.content.decode('utf-8'))['user_message']

    @patch('lms.djangoapps.teams.views.can_user_create_team_in_topic', return_value=False)
    @patch('lms.djangoapps.teams.views.has_specific_teamset_access', return_value=True)
    def test_student_create_team_instructor_managed_topic(self, *args):  # pylint: disable=unused-argument
        response = self.post_create_team(
            403,
            data=self.build_team_data(
                name="student create team in instructor managed topic",
                course=self.test_course_1,
                description="student cannot create team in instructor-managed topic",
                topic_id='private_topic_1_id'
            ),
            user='student_enrolled_not_on_team'
        )
        assert "You can't create a team in an instructor managed topic." == \
               json.loads(response.content.decode('utf-8'))['user_message']

    @ddt.data('staff', 'course_staff', 'community_ta')
    def test_privileged_create_multiple_teams(self, user):
        """ Privileged users can create multiple teams, even if they are already in one. """
        # First add the privileged user to a team.
        self.post_create_membership(
            200,
            self.build_membership_data(user, self.solar_team),
            user=user
        )

        self.post_create_team(
            data=self.build_team_data(
                name="Another team",
                course=self.test_course_1,
                description="Privileged users are the best",
                topic_id=self.solar_team.topic_id
            ),
            user=user
        )

    @ddt.data({'description': ''}, {'name': 'x' * 1000}, {'name': ''})
    def test_bad_fields(self, kwargs):
        self.post_create_team(400, self.build_team_data(**kwargs))

    def test_missing_name(self):
        self.post_create_team(400, {
            'course_id': str(self.test_course_1.id),
            'description': "foobar"
        })

    def test_full_student_creator(self):
        creator = self.create_and_enroll_student()
        team = self.post_create_team(data=self.build_team_data(
            name="Fully specified team",
            course=self.test_course_1,
            description="Another fantastic team",
            topic_id='topic_1',
            country='CA',
            language='fr'
        ), user=creator)

        # Verify the id (it ends with a unique hash, which is the same as the discussion_id).
        self.verify_expected_team_id(team, 'fully-specified-team')
        del team['id']

        self.assert_event_emitted(
            'edx.team.created',
            team_id=self._expected_team_id(team, 'fully-specified-team'),
        )

        self.assert_event_emitted(
            'edx.team.learner_added',
            team_id=self._expected_team_id(team, 'fully-specified-team'),
            user_id=self.users[creator].id,
            add_method='added_on_create'
        )
        # Remove date_created and discussion_topic_id because they change between test runs
        del team['date_created']
        del team['discussion_topic_id']

        # Since membership is its own list, we want to examine this separately.
        team_membership = team['membership']
        del team['membership']

        # verify that it's been set to a time today.
        assert parser.parse(team['last_activity_at']).date() == datetime.utcnow().replace(tzinfo=pytz.utc).date()
        del team['last_activity_at']

        # Verify that the creating user gets added to the team.
        assert len(team_membership) == 1
        member = team_membership[0]['user']
        assert member['username'] == creator

        assert team == {
            'name': 'Fully specified team', 'language': 'fr', 'country': 'CA', 'topic_id': 'topic_1',
            'course_id': str(self.test_course_1.id), 'description': 'Another fantastic team',
            'organization_protected': False
        }

    @ddt.data('staff', 'course_staff', 'community_ta')
    def test_membership_staff_creator(self, user):
        # Verify that staff do not automatically get added to a team
        # when they create one.
        team = self.post_create_team(data=self.build_team_data(
            name="New team",
            course=self.test_course_1,
            description="Another fantastic team",
        ), user=user)

        assert team['membership'] == []

    @ddt.unpack
    @ddt.data(
        ('student_enrolled', 404, None),
        ('student_unenrolled', 403, None),
        ('student_enrolled_not_on_team', 404, None),
        ('student_masters', 404, None),
        ('student_on_team_1_private_set_1', 403, "You can't create a team in an instructor managed topic."),
        ('student_on_team_2_private_set_1', 403, "You can't create a team in an instructor managed topic."),
        ('staff', 200, None)
    )
    def test_private_managed_access(self, user, expected_response, msg):
        """
        As different users, check if we can create a team in a private teamset.
        Only staff should be able to create teams in managed teamsets, but they're also
        the only ones who should know that private_managed teamsets exist. If the team hasn't been created yet,
        no one can be in it, so no non-staff should get any info at all from this endpoint.
        """
        response = self.post_create_team(
            expected_response,
            data=self.build_team_data(
                name="test_private_managed_access",
                course=self.test_course_1,
                description="test_private_managed_access",
                topic_id="private_topic_1_id"
            ),
            user=user
        )
        if msg:
            assert msg == response.json()['user_message']


@ddt.ddt
class TestDetailTeamAPI(TeamAPITestCase):
    """Test cases for the team detail endpoint."""

    @ddt.data(
        (None, 401),
        ('student_inactive', 403),
        ('student_unenrolled', 403),
        ('student_enrolled', 200),
        ('student_enrolled_inactive', 200),
        ('staff', 200),
        ('course_staff', 200),
        ('community_ta', 200),
    )
    @ddt.unpack
    def test_access(self, user, status):
        team = self.get_team_detail(self.solar_team.team_id, status, user=user)
        if status == 200:
            assert team['description'] == self.solar_team.description
            assert team['discussion_topic_id'] == self.solar_team.discussion_topic_id
            assert parser.parse(team['last_activity_at']) == LAST_ACTIVITY_AT

    def test_does_not_exist(self):
        self.get_team_detail('no_such_team', 404)

    def test_expand_private_user(self):
        # Use the default user which is already private because to year_of_birth is set
        result = self.get_team_detail(self.solar_team.team_id, 200, {'expand': 'user'})
        self.verify_expanded_private_user(result['membership'][0]['user'])

    def test_expand_public_user(self):
        result = self.get_team_detail(
            self.public_profile_team.team_id,
            200,
            {'expand': 'user'},
            user='student_enrolled_public_profile'
        )
        self.verify_expanded_public_user(result['membership'][0]['user'])

    @ddt.unpack
    @ddt.data(
        ('student_unenrolled', 403),
        ('student_enrolled', 404),
        ('student_masters_not_on_team', 200),
        ('student_masters', 200),
        ('staff', 200)
    )
    def test_organization_protected(self, requesting_user, expected_response):
        """
        As different users, check if we can request the masters_only team detail.
        Only staff and users within the organization_protection bubble should be able to get info about
        an organization_protected team, or be able to tell that it exists.
        """
        team = self.get_team_detail(
            self.masters_only_team.team_id,
            expected_response,
            user=requesting_user
        )
        if expected_response == 200:
            assert team['name'] == self.masters_only_team.name

    @ddt.unpack
    @ddt.data(
        ('student_unenrolled', 403),
        ('student_enrolled', 404),
        ('student_masters', 404),
        ('student_on_team_1_private_set_1', 200),
        ('student_on_team_2_private_set_1', 404),
        ('staff', 200)
    )
    def test_teamset_types(self, requesting_user, expected_response):
        """
        As different users, check if we can request the masters_only team detail.
        Only staff or users enrolled in the team should be able to get info about a private_managed team,
        or even be able to tell that it exists.
        """
        team = self.get_team_detail(
            self.team_1_in_private_teamset_1.team_id,
            expected_response,
            user=requesting_user
        )
        if expected_response == 200:
            assert team['name'] == self.team_1_in_private_teamset_1.name


@ddt.ddt
class TestDeleteTeamAPI(EventTestMixin, TeamAPITestCase):
    """Test cases for the team delete endpoint."""

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp('lms.djangoapps.teams.utils.tracker')

    @ddt.data(
        ('staff', 204),
        ('course_staff', 204),
        ('community_ta', 204),
        ('admin', 204)
    )
    @ddt.unpack
    def test_access(self, user, status):
        team_list = self.get_teams_list(user='course_staff', expected_status=200)
        previous_count = team_list['count']
        assert self.solar_team.team_id in [result['id'] for result in team_list.get('results')]
        self.delete_team(self.solar_team.team_id, status, user=user)

        team_list = self.get_teams_list(user='course_staff', expected_status=200)
        assert team_list['count'] == (previous_count - 1)
        assert self.solar_team.team_id not in [result['id'] for result in team_list.get('results')]
        self.assert_event_emitted(
            'edx.team.deleted',
            team_id=self.solar_team.team_id,
        )
        self.assert_event_emitted(
            'edx.team.learner_removed',
            team_id=self.solar_team.team_id,
            remove_method='team_deleted',
            user_id=self.users['student_enrolled'].id
        )

    @ddt.data(
        ('student_unenrolled', 403),
        ('student_enrolled', 403),
        ('student_inactive', 403),
    )
    @ddt.unpack
    def test_access_forbidden(self, user, status):
        team_list = self.get_teams_list(user='course_staff', expected_status=200)
        previous_count = team_list['count']
        assert self.solar_team.team_id in [result['id'] for result in team_list.get('results')]
        self.delete_team(self.solar_team.team_id, status, user=user)

        team_list = self.get_teams_list(user='course_staff', expected_status=200)
        assert team_list['count'] == previous_count
        assert self.solar_team.team_id in [result['id'] for result in team_list.get('results')]

    @ddt.data(
        (None, 401),
    )
    @ddt.unpack
    def test_access_unauthorized(self, user, status):
        self.delete_team(self.solar_team.team_id, status, user=user)

    def test_does_not_exist(self):
        self.delete_team('nonexistent', 404)

    def test_memberships_deleted(self):
        assert CourseTeamMembership.objects.filter(team=self.solar_team).count() == 1
        self.delete_team(self.solar_team.team_id, 204, user='staff')
        self.assert_event_emitted(
            'edx.team.deleted',
            team_id=self.solar_team.team_id,
        )
        self.assert_event_emitted(
            'edx.team.learner_removed',
            team_id=self.solar_team.team_id,
            remove_method='team_deleted',
            user_id=self.users['student_enrolled'].id
        )
        assert CourseTeamMembership.objects.filter(team=self.solar_team).count() == 0

    @ddt.unpack
    @ddt.data(
        ('student_unenrolled', 403),
        ('student_enrolled', 404),
        ('student_masters_not_on_team', 403),
        ('student_masters', 403),
        ('staff', 204)
    )
    def test_organization_protection_status(self, requesting_user, expected_status):
        """
        As different users, try to delete the masters-only team.
        Only staff should be able to delete this team, and people outside the bubble shouldn't be able to
        tell that it even exists.
        """
        self.delete_team(
            self.masters_only_team.team_id,
            expected_status,
            user=requesting_user
        )

    @ddt.unpack
    @ddt.data(
        ('student_unenrolled', 403),
        ('student_enrolled', 404),
        ('student_on_team_1_private_set_1', 403),
        ('student_on_team_2_private_set_1', 404),
        ('staff', 204)
    )
    def test_teamset_type(self, requesting_user, expected_status):
        """
        As different users, try to delete a private_managed team
        Only staff should be able to delete a private_managed team, and only they and users enrolled in that
        team should even be able to tell that it exists.
        """
        self.delete_team(
            self.team_1_in_private_teamset_1.team_id,
            expected_status,
            user=requesting_user
        )


@ddt.ddt
class TestUpdateTeamAPI(EventTestMixin, TeamAPITestCase):
    """Test cases for the team update endpoint."""

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp('lms.djangoapps.teams.utils.tracker')

    @ddt.data(
        (None, 401),
        ('student_inactive', 403),
        ('student_unenrolled', 403),
        ('student_enrolled', 403),
        ('student_enrolled_inactive', 403),
        ('staff', 200),
        ('course_staff', 200),
        ('community_ta', 200),
    )
    @ddt.unpack
    def test_access(self, user, status):
        prev_name = self.solar_team.name
        team = self.patch_team_detail(self.solar_team.team_id, status, {'name': 'foo'}, user=user)
        if status == 200:
            assert team['name'] == 'foo'
            self.assert_event_emitted(
                'edx.team.changed',
                team_id=self.solar_team.team_id,
                truncated=[],
                field='name',
                old=prev_name,
                new='foo'
            )

    @ddt.data(
        (None, 401),
        ('student_inactive', 404),
        ('student_unenrolled', 404),
        ('student_enrolled', 404),
        ('staff', 404),
        ('course_staff', 404),
        ('community_ta', 404),
    )
    @ddt.unpack
    def test_access_bad_id(self, user, status):
        self.patch_team_detail("no_such_team", status, {'name': 'foo'}, user=user)

    @ddt.data(
        ('id', 'foobar'),
        ('description', ''),
        ('country', 'no_such_country'),
        ('language', 'no_such_language')
    )
    @ddt.unpack
    def test_bad_requests(self, key, value):
        self.patch_team_detail(self.solar_team.team_id, 400, {key: value}, user='staff')

    @ddt.data(('country', 'US'), ('language', 'en'), ('foo', 'bar'))
    @ddt.unpack
    def test_good_requests(self, key, value):
        if hasattr(self.solar_team, key):
            prev_value = getattr(self.solar_team, key)

        self.patch_team_detail(self.solar_team.team_id, 200, {key: value}, user='staff')

        if hasattr(self.solar_team, key):
            self.assert_event_emitted(
                'edx.team.changed',
                team_id=self.solar_team.team_id,
                truncated=[],
                field=key,
                old=prev_value,
                new=value
            )

    def test_does_not_exist(self):
        self.patch_team_detail('no_such_team', 404, user='staff')

    @ddt.unpack
    @ddt.data(
        ('student_unenrolled', 403),
        ('student_enrolled', 404),
        ('student_masters_not_on_team', 403),
        ('student_masters', 403),
        ('staff', 200)
    )
    def test_organization_protection_status(self, requesting_user, expected_status):
        """
        As different users, try to modify the masters-only team.
        Only staff should be able to modify this team, and people outside the bubble shouldn't be able to
        tell that it even exists.
        """
        team = self.patch_team_detail(
            self.masters_only_team.team_id,
            expected_status,
            {'name': 'foo'},
            user=requesting_user
        )
        if expected_status == 200:
            assert team['name'] == 'foo'

    @ddt.unpack
    @ddt.data(
        ('student_unenrolled', 403),
        ('student_enrolled', 404),
        ('student_on_team_1_private_set_1', 403),
        ('student_on_team_2_private_set_1', 404),
        ('staff', 200)
    )
    def test_teamset_type(self, requesting_user, expected_status):
        """
        As different users, try to modify a private_managed team
        Only staff should be able to modify a private_managed team, and only they and users enrolled in that
        team should even be able to tell that it exists.
        """
        team = self.patch_team_detail(
            self.team_1_in_private_teamset_1.team_id,
            expected_status,
            {'name': 'foo'},
            user=requesting_user
        )
        if expected_status == 200:
            assert team['name'] == 'foo'


@override_settings(ENABLE_ORA_TEAM_SUBMISSIONS=True)
@ddt.ddt
class TestTeamAssignmentsView(TeamAPITestCase):
    """ Tests for the TeamAssignmentsView """

    @classmethod
    def setUpClass(cls):
        """ Create an openassessment block for testing """
        super().setUpClass()

        course = cls.test_course_1
        teamset_id = cls.solar_team.topic_id
        other_teamset_id = cls.wind_team.topic_id

        section = BlockFactory.create(
            parent=course,
            category='chapter',
            display_name='Test Section'
        )
        subsection = BlockFactory.create(
            parent=section,
            category="sequential"
        )
        unit_1 = BlockFactory.create(
            parent=subsection,
            category="vertical"
        )
        open_assessment = BlockFactory.create(
            parent=unit_1,
            category="openassessment",
            teams_enabled=True,
            selected_teamset_id=teamset_id
        )
        unit_2 = BlockFactory.create(
            parent=subsection,
            category="vertical"
        )
        off_team_open_assessment = BlockFactory.create(  # pylint: disable=unused-variable
            parent=unit_2,
            category="openassessment",
            teams_enabled=True,
            selected_teamset_id=other_teamset_id
        )

        cls.team_assignments = [open_assessment]

    @ddt.unpack
    @ddt.data(
        (None, 401),
        ('student_inactive', 403),
        ('student_unenrolled', 403),
        ('student_on_team_2_private_set_1', 404),
        ('student_enrolled', 200),
        ('student_enrolled_inactive', 200),
        ('staff', 200),
        ('course_staff', 200),
        ('community_ta', 200),
    )
    def test_get_assignments(self, user, expected_status):
        # Given a course with team-enabled open responses
        team_id = self.solar_team.team_id

        # When I get the assignments for a team
        assignments = self.get_team_assignments(team_id, expected_status, user=user)

        if expected_status == 200:
            # I successful, I get back the assignments for a team
            assert len(assignments) == len(self.team_assignments)

            # ... with the right data structure
            for assignment in assignments:
                assert 'display_name' in assignment.keys()
                assert 'location' in assignment.keys()

    def test_get_assignments_bad_team(self):
        # Given a bad team is supplied
        user = 'student_enrolled'
        team_id = 'bogus-team'

        # When I run the query, I get back a 404 error
        expected_status = 404
        self.get_team_assignments(team_id, expected_status, user=user)

    @override_settings(ENABLE_ORA_TEAM_SUBMISSIONS=False)
    def test_get_assignments_feature_not_enabled(self):
        # Given the team submissions feature is not enabled
        user = 'student_enrolled'
        team_id = self.solar_team.team_id

        # When I try to get assignments
        # Then I get back a 503 error
        expected_status = 503
        self.get_team_assignments(team_id, expected_status, user=user)


@ddt.ddt
class TestListTopicsAPI(TeamAPITestCase):
    """Test cases for the topic listing endpoint."""

    @ddt.data(
        (None, 401, None),
        ('student_inactive', 403, None),
        ('student_unenrolled', 403, None),
        ('student_enrolled', 200, 4),
        ('student_enrolled_inactive', 200, 4),
        ('staff', 200, 7),
        ('course_staff', 200, 7),
        ('community_ta', 200, 4),
    )
    @ddt.unpack
    def test_access(self, user, status, expected_topics_count):
        topics = self.get_topics_list(status, {'course_id': str(self.test_course_1.id)}, user=user)
        if status == 200:
            assert topics['count'] == expected_topics_count

    @ddt.data('A+BOGUS+COURSE', 'A/BOGUS/COURSE')
    def test_invalid_course_key(self, course_id):
        self.get_topics_list(404, {'course_id': course_id})

    def test_without_course_id(self):
        self.get_topics_list(400)

    @ddt.data(
        (None, 200, ['Coal Power', 'Nuclear Power', 'Sólar power', 'Wind Power'], 'name'),
        ('name', 200, ['Coal Power', 'Nuclear Power', 'Sólar power', 'Wind Power'], 'name'),
        # Note that "Nuclear Power" will have 2 teams. "Coal Power" "Wind Power" and "Solar Power"
        # all have 1 team. The secondary sort is alphabetical by name.
        ('team_count', 200, ['Nuclear Power', 'Coal Power', 'Sólar power', 'Wind Power'], 'team_count'),
        ('no_such_field', 400, [], None),
    )
    @ddt.unpack
    def test_order_by(self, field, status, names, expected_ordering):
        with skip_signal(
            post_save,
            receiver=course_team_post_save_callback,
            sender=CourseTeam,
            dispatch_uid='teams.signals.course_team_post_save_callback'
        ):
            # Add a team to "Nuclear Power", so it has two teams
            CourseTeamFactory.create(
                name='Nuclear Team 1', course_id=self.test_course_1.id, topic_id='topic_2'
            )
            # Add a team to "Coal Power", so it has one team, same as "Wind" and "Solar"
            CourseTeamFactory.create(
                name='Coal Team 1', course_id=self.test_course_1.id, topic_id='topic_3'
            )
        data = {'course_id': str(self.test_course_1.id)}
        if field:
            data['order_by'] = field
        topics = self.get_topics_list(status, data, user='student_enrolled')
        if status == 200:
            assert names == [topic['name'] for topic in topics['results']]
            assert topics['sort_order'] == expected_ordering

    def test_order_by_team_count_secondary(self):
        """
        Ensure that the secondary sort (alphabetical) when primary sort is team_count
        works across pagination boundaries.
        """
        # All teams have one teamset, except for Coal Power, topic_3
        with skip_signal(
            post_save,
            receiver=course_team_post_save_callback,
            sender=CourseTeam,
            dispatch_uid='teams.signals.course_team_post_save_callback'
        ):
            # Add two wind teams, a solar team and a coal team, to bring the totals to
            # Wind: 3 Solar: 2 Coal: 1, Nuclear: 1
            CourseTeamFactory.create(
                name='Wind Team 1', course_id=self.test_course_1.id, topic_id='topic_1'
            )
            CourseTeamFactory.create(
                name='Wind Team 2', course_id=self.test_course_1.id, topic_id='topic_1'
            )
            CourseTeamFactory.create(
                name='Solar Team 1', course_id=self.test_course_1.id, topic_id='topic_0'
            )
            CourseTeamFactory.create(
                name='Coal Team 1', course_id=self.test_course_1.id, topic_id='topic_3'
            )

        # Wind power has the most teams, followed by Solar
        topics = self.get_topics_list(
            data={
                'course_id': str(self.test_course_1.id),
                'page_size': 2,
                'page': 1,
                'order_by': 'team_count'
            },
            user='student_enrolled'
        )
        assert ['Wind Power', 'Sólar power'] == [topic['name'] for topic in topics['results']]

        # Coal and Nuclear are tied, so they are alphabetically sorted.
        topics = self.get_topics_list(
            data={
                'course_id': str(self.test_course_1.id),
                'page_size': 2,
                'page': 2,
                'order_by': 'team_count'
            },
            user='student_enrolled'
        )
        assert ['Coal Power', 'Nuclear Power'] == [topic['name'] for topic in topics['results']]

    def test_pagination(self):
        response = self.get_topics_list(
            data={
                'course_id': str(self.test_course_1.id),
                'page_size': 2,
            },
            user='student_enrolled'
        )

        assert 2 == len(response['results'])
        assert 'next' in response
        assert 'previous' in response
        assert response['previous'] is None
        assert response['next'] is not None

    def test_default_ordering(self):
        response = self.get_topics_list(data={'course_id': str(self.test_course_1.id)})
        assert response['sort_order'] == 'name'

    def test_team_count(self):
        """Test that team_count is included for each topic"""
        response = self.get_topics_list(
            data={'course_id': str(self.test_course_1.id)},
            user='student_enrolled'
        )
        for topic in response['results']:
            assert 'team_count' in topic
            if topic['id'] in ('topic_0', 'topic_1', 'topic_2'):
                assert topic['team_count'] == 1
            else:
                assert topic['team_count'] == 0

    @ddt.unpack
    @ddt.data(
        ('student_enrolled', 0),
        ('student_on_team_1_private_set_1', 1),
        ('student_on_team_2_private_set_1', 1),
        ('student_masters', 0),
        ('staff', 3)
    )
    def test_teamset_type(self, requesting_user, expected_private_teamsets):
        """
        As different users, request course_1's list of topics, and see what private_managed teamsets are returned

        Staff should be able to see all teamsets, and anyone enrolled in a private teamset should see that and
        only that teamset
        """
        topics = self.get_topics_list(
            data={'course_id': str(self.test_course_1.id)},
            user=requesting_user
        )
        private_teamsets_returned = [
            topic['name'] for topic in topics['results'] if topic['type'] == 'private_managed'
        ]
        assert len(private_teamsets_returned) == expected_private_teamsets

    @ddt.unpack
    @ddt.data(
        ('student_on_team_1_private_set_1', 1),
        ('student_on_team_2_private_set_1', 1),
        ('staff', 2)
    )
    def test_private_teamset_team_count(self, requesting_user, expected_team_count):
        """
        Students should only see teams they are members of in private team-sets
        """
        topics = self.get_topics_list(
            data={'course_id': str(self.test_course_1.id)},
            user=requesting_user
        )
        private_teamset_1 = [topic for topic in topics['results'] if topic['name'] == 'private_topic_1_name'][0]
        assert private_teamset_1['team_count'] == expected_team_count


@ddt.ddt
class TestDetailTopicAPI(TeamAPITestCase):
    """Test cases for the topic detail endpoint."""

    @ddt.data(
        (None, 401),
        ('student_inactive', 403),
        ('student_unenrolled', 403),
        ('student_enrolled', 200),
        ('student_enrolled_inactive', 200),
        ('staff', 200),
        ('course_staff', 200),
        ('community_ta', 200),
    )
    @ddt.unpack
    def test_access(self, user, status):
        topic = self.get_topic_detail('topic_0', self.test_course_1.id, status, user=user)
        if status == 200:
            for field in ('id', 'name', 'description'):
                assert field in topic

    @ddt.data('A+BOGUS+COURSE', 'A/BOGUS/COURSE')
    def test_invalid_course_id(self, course_id):
        self.get_topic_detail('topic_0', course_id, 404)

    def test_invalid_topic_id(self):
        self.get_topic_detail('no_such_topic', self.test_course_1.id, 404)

    def test_topic_detail_with_caps_and_dot_in_id(self):
        self.get_topic_detail('Topic_6.5', self.test_course_2.id, user='student_enrolled_public_profile')

    def test_team_count(self):
        """Test that team_count is included with a topic"""
        topic = self.get_topic_detail(topic_id='topic_0', course_id=self.test_course_1.id)
        assert topic['team_count'] == 1
        topic = self.get_topic_detail(topic_id='topic_1', course_id=self.test_course_1.id)
        assert topic['team_count'] == 1
        topic = self.get_topic_detail(topic_id='topic_2', course_id=self.test_course_1.id)
        assert topic['team_count'] == 1
        topic = self.get_topic_detail(topic_id='topic_3', course_id=self.test_course_1.id)
        assert topic['team_count'] == 0

    @ddt.unpack
    @ddt.data(
        ('student_enrolled', 404, None),
        ('student_on_team_1_private_set_1', 200, 1),
        ('student_on_team_2_private_set_1', 200, 1),
        ('student_masters', 404, None),
        ('staff', 200, 2)
    )
    def test_teamset_type(self, requesting_user, expected_status, expected_team_count):
        """
        As different users, request info about a private_managed team.
        Staff should be able to see all teamsets, and someone enrolled in a private_managed teamset
        should be able to see that and only that teamset. As shown in `test_invalid_topic_id`,
        nonexistant topics 404, and if someone doesn't have access to a private_managed teamset, as far as they know
        the teamset does not exist.
        """
        topic = self.get_topic_detail(
            topic_id='private_topic_1_id',
            course_id=self.test_course_1.id,
            expected_status=expected_status,
            user=requesting_user
        )
        if expected_status == 200:
            assert topic['name'] == 'private_topic_1_name'
            assert topic['team_count'] == expected_team_count


@ddt.ddt
class TestListMembershipAPI(TeamAPITestCase):
    """Test cases for the membership list endpoint."""

    @ddt.data(
        (None, 401),
        ('student_inactive', 404),
        ('student_unenrolled', 404),
        ('student_enrolled', 200),
        ('student_enrolled_inactive', 200),
        ('student_enrolled_both_courses_other_team', 200),
        ('staff', 200),
        ('course_staff', 200),
        ('community_ta', 200),
    )
    @ddt.unpack
    def test_access(self, user, status):
        membership = self.get_membership_list(status, {'team_id': self.solar_team.team_id}, user=user)
        if status == 200:
            assert membership['count'] == 1
            assert membership['results'][0]['user']['username'] == self.users['student_enrolled'].username

    @ddt.data(
        (None, 401, False),
        ('student_inactive', 200, False),
        ('student_unenrolled', 200, False),
        ('student_enrolled', 200, True),
        ('student_enrolled_both_courses_other_team', 200, True),
        ('staff', 200, True),
        ('course_staff', 200, True),
        ('community_ta', 200, True),
    )
    @ddt.unpack
    def test_access_by_username(self, user, status, has_content):
        membership = self.get_membership_list(status, {'username': self.users['student_enrolled'].username}, user=user)
        if status == 200:
            if has_content:
                assert membership['count'] == 1
                assert membership['results'][0]['team']['team_id'] == self.solar_team.team_id
            else:
                assert membership['count'] == 0

    @ddt.data(
        ('student_masters', True),
        ('student_masters_not_on_team', True),
        ('student_unenrolled', False),
        ('student_enrolled', True),
        ('student_enrolled_both_courses_other_team', True),
        ('staff', True),
    )
    @ddt.unpack
    def test_access_by_username_organization_protected(self, user, can_see_bubble_team):
        """
        As different users, request team membership info for student_masters
        Only staff, and users who are within the bubble should be able to see a bubble user's team
        memberships. Non-bubble users shouldn't be able to tell that student_masters exists.
        (Nonexistant users still return 200, just with no data.)

        TODO: Only the oragnization_protected users (student_masters, student_masters_not_on_team)
        and staff should be able to see student_masters
        """
        membership = self.get_membership_list(200, {'username': 'student_masters'}, user=user)
        if can_see_bubble_team:
            assert membership['count'] == 1
            assert membership['results'][0]['team']['team_id'] == self.masters_only_team.team_id
        else:
            assert membership['count'] == 0

    @ddt.unpack
    @ddt.data(
        ('student_on_team_1_private_set_1', True, True),
        ('student_unenrolled', False, False),
        ('student_enrolled', True, True),
        ('student_on_team_2_private_set_1', True, True),
        ('student_masters', True, True),
        ('staff', True, True)
    )
    def test_access_by_username_private_teamset(self, user, can_see_any_teams, can_see_private_team):
        """
        Add student_on_team_1_private_set_1 to masters_only_team.
        Then, as different users, request team membership info for student_on_team_1_private_set_1.
        Anyone in the organization_protected bubble should be able to see the masters_only membership,
        but only staff and users in team_1_private_set_1 ahould be able to see that membership.

        TODO: student_enrolled shouldn't see any teams as he is outside the bubble.
        student_masters and sot2ps1 should only see masters_only team.
        """
        self.masters_only_team.add_user(self.users['student_on_team_1_private_set_1'])
        memberships = self.get_membership_list(200, {'username': 'student_on_team_1_private_set_1'}, user=user)
        team_ids = [membership['team']['team_id'] for membership in memberships['results']]
        if can_see_private_team:
            assert len(team_ids) == 2
            assert self.team_1_in_private_teamset_1.team_id in team_ids
            assert self.masters_only_team.team_id in team_ids
        elif can_see_any_teams:
            assert len(team_ids) == 1
            assert self.masters_only_team.team_id in team_ids
        else:
            assert len(team_ids) == 0

    @ddt.unpack
    @ddt.data(
        ('student_on_team_1_private_set_1', 200),
        ('student_unenrolled', 404),
        ('student_enrolled', 403),
        ('student_on_team_2_private_set_1', 403),
        ('student_masters', 403),
        ('staff', 200)
    )
    def test_access_by_team_private_teamset(self, user, expected_response):
        """
        As different users, request membership info for team_1_in_private_teamset_1.
        Only staff or users enrolled in a private_managed team should be able to tell that the team exists.
        (a bad team_id returns a 404 currently)

        TODO: No data is returned that shouldn't be, but the 403 that the users get tells them that a team
        with the given id does in fact exist. This should be changed to be a 404.
        """
        memberships = self.get_membership_list(
            expected_response,
            {'team_id': self.team_1_in_private_teamset_1.team_id},
            user=user
        )
        if expected_response == 200:
            users = [membership['user']['username'] for membership in memberships['results']]
            assert users == ['student_on_team_1_private_set_1']

    @ddt.data(
        ('student_enrolled_both_courses_other_team', 'course-v1:TestX+TS101+Test_Course', 200, 'Nuclear Team'),
        ('student_enrolled_both_courses_other_team', 'course-v1:MIT+6.002x+Circuits', 200, 'Another Team'),
        ('student_enrolled', 'course-v1:TestX+TS101+Test_Course', 200, 'Sólar team'),
        ('student_enrolled', 'course-v1:MIT+6.002x+Circuits', 400, ''),
    )
    @ddt.unpack
    def test_course_filter_with_username(self, user, course_id, status, team_name):
        membership = self.get_membership_list(
            status,
            {
                'username': self.users[user],
                'course_id': course_id
            },
            user=user
        )
        if status == 200:
            assert membership['count'] == 1
            assert membership['results'][0]['team']['team_id'] == self.test_team_name_id_map[team_name].team_id

    @ddt.data(
        ('course-v1:TestX+TS101+Test_Course', 200),
        ('course-v1:MIT+6.002x+Circuits', 400),
    )
    @ddt.unpack
    def test_course_filter_with_team_id(self, course_id, status):
        membership = self.get_membership_list(status, {'team_id': self.solar_team.team_id, 'course_id': course_id})
        if status == 200:
            assert membership['count'] == 1
            assert membership['results'][0]['team']['team_id'] == self.solar_team.team_id

    def test_nonexistent_user(self):
        response = self.get_membership_list(200, {'username': 'this-user-will-not-exist-&&&&#!^'})
        assert response['count'] == 0

    def test_bad_course_id(self):
        self.get_membership_list(404, {'course_id': 'no_such_course'})

    def test_no_username_or_team_id(self):
        self.get_membership_list(400, {})

    def test_bad_team_id(self):
        self.get_membership_list(404, {'team_id': 'no_such_team'})

    def test_expand_private_user(self):
        # Use the default user which is already private because to year_of_birth is set
        result = self.get_membership_list(200, {'team_id': self.solar_team.team_id, 'expand': 'user'})
        self.verify_expanded_private_user(result['results'][0]['user'])

    def test_expand_public_user(self):
        result = self.get_membership_list(
            200,
            {'team_id': self.public_profile_team.team_id, 'expand': 'user'},
            user='student_enrolled_public_profile'
        )
        self.verify_expanded_public_user(result['results'][0]['user'])

    def test_expand_team(self):
        result = self.get_membership_list(200, {'team_id': self.solar_team.team_id, 'expand': 'team'})
        self.verify_expanded_team(result['results'][0]['team'])

    @ddt.data(False, True)
    def test_filter_teamset(self, filter_username):
        other_username = self.create_and_enroll_student()
        self.solar_team.add_user(self.users[other_username])
        filters = {
            'teamset_id': self.solar_team.topic_id,
            'course_id': str(self.test_course_1.id)
        }
        if filter_username:
            filters['username'] = other_username

        result = self.get_membership_list(200, filters)
        assert result['count'] == (1 if filter_username else 2)
        usernames = {enrollment['user']['username'] for enrollment in result['results']}
        assert other_username in usernames
        if not filter_username:
            assert 'student_enrolled' in usernames

    def test_filter_teamset_team_id(self):
        # team_id and teamset_id are mutually exclusive
        self.get_membership_list(
            400,
            {
                'team_id': self.solar_team.team_id,
                'teamset_id': 'topic_0',
                'course_id': 'course-v1:TestX+TS101+Non_Existent_Course'
            }
        )

    def test_filter_teamset_no_course(self):
        self.get_membership_list(400, {'teamset_id': 'topic_0'})

    def test_filter_teamset_not_enrolled_in_course(self):
        self.get_membership_list(
            404,
            {
                'teamset_id': 'topic_0',
                'course_id': str(self.test_course_1.id)
            },
            user='student_unenrolled'
        )

    def test_filter_teamset_course_nonexistant(self):
        self.get_membership_list(404, {'teamset_id': 'topic_0',
                                       'course_id': 'course-v1:TestX+TS101+Non_Existent_Course'})

    def test_filter_teamset_teamset_nonexistant(self):
        self.get_membership_list(404, {'teamset_id': 'nonexistant', 'course_id': str(self.test_course_1.id)})

    def test_filter_teamset_enrolled_in_course_but_no_team_access(self):
        # The requesting user is enrolled in the course, but the requested team is oraganization_protected and
        # the requesting user is outside of the bubble
        self.get_membership_list(
            404,
            {
                'teamset_id': 'private_topic_1_id',
                'course_id': str(self.test_course_1.id),
                'username': 'student_on_team_1_private_set_1'
            }
        )

    @ddt.unpack
    @ddt.data(
        ('student_enrolled', 404, None),
        ('student_on_team_1_private_set_1', 200, {'student_on_team_1_private_set_1'}),
        ('student_on_team_2_private_set_1', 200, {'student_on_team_2_private_set_1'}),
        ('student_masters', 404, None),
        ('staff', 200, {'student_on_team_1_private_set_1', 'student_on_team_2_private_set_1'})
    )
    def test_access_filter_teamset__private_teamset(self, user, expected_response, expected_users):
        memberships = self.get_membership_list(
            expected_response,
            {
                'teamset_id': 'private_topic_1_id',
                'course_id': str(self.test_course_1.id),
            },
            user=user
        )
        if expected_response == 200:
            returned_users = {membership['user']['username'] for membership in memberships['results']}
            assert returned_users == expected_users

    @ddt.unpack
    @ddt.data(
        ('student_enrolled', 404),
        ('student_on_team_1_private_set_1', 404),
        ('student_on_team_2_private_set_1', 404),
        ('student_masters', 404),
        ('staff', 200)
    )
    def test_access_filter_teamset__private_teamset__no_teams(self, user, expected_response):
        """
        private_topic_no_teams has no teams in it, but staff should still get a 200 when
        requesting teamset memberships
        """
        self.get_membership_list(
            expected_response,
            {
                'teamset_id': 'private_topic_no_teams',
                'course_id': str(self.test_course_1.id),
            },
            user=user
        )

    @ddt.unpack
    @ddt.data(
        ('student_unenrolled', 404, {}),
        ('student_enrolled_not_on_team', 200, {'student_enrolled'}),
        ('student_enrolled', 200, {'student_enrolled'}),
        ('student_masters', 200, {'student_masters'}),
        ('staff', 200, {'student_enrolled', 'student_masters'})
    )
    def test_access_filter_teamset__open_teamset(self, user, expected_response, expected_usernames):
        # topic_3 has no teams
        assert not CourseTeam.objects.filter(topic_id='topic_3').exists()
        memberships = self.get_membership_list(
            expected_response,
            {
                'teamset_id': 'topic_3',
                'course_id': str(self.test_course_1.id),
            },
            user=user
        )
        if expected_response == 200:
            assert memberships['count'] == 0

        # topic_0 has teams
        assert CourseTeam.objects.filter(topic_id='topic_0').exists()
        memberships = self.get_membership_list(
            expected_response,
            {
                'teamset_id': 'topic_0',
                'course_id': str(self.test_course_1.id),
            },
            user=user
        )
        if expected_response == 200:
            returned_users = {membership['user']['username'] for membership in memberships['results']}
            assert returned_users == expected_usernames


@ddt.ddt
class TestCreateMembershipAPI(EventTestMixin, TeamAPITestCase):
    """Test cases for the membership creation endpoint."""

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp('lms.djangoapps.teams.utils.tracker')

    @ddt.data(
        (None, 401),
        ('student_inactive', 404),
        ('student_unenrolled', 404),
        ('student_enrolled_not_on_team', 200),
        ('student_enrolled', 404),
        ('student_enrolled_inactive', 404),
        ('student_enrolled_both_courses_other_team', 404),
        ('staff', 200),
        ('course_staff', 200),
        ('community_ta', 200),
    )
    @ddt.unpack
    def test_access(self, user, status):
        membership = self.post_create_membership(
            status,
            self.build_membership_data('student_enrolled_not_on_team', self.solar_team),
            user=user
        )
        if status == 200:
            assert membership['user']['username'] == self.users['student_enrolled_not_on_team'].username
            assert membership['team']['team_id'] == self.solar_team.team_id
            memberships = self.get_membership_list(200, {'team_id': self.solar_team.team_id})
            assert memberships['count'] == 2

            add_method = 'joined_from_team_view' if user == 'student_enrolled_not_on_team' else 'added_by_another_user'

            self.assert_event_emitted(
                'edx.team.learner_added',
                team_id=self.solar_team.team_id,
                user_id=self.users['student_enrolled_not_on_team'].id,
                add_method=add_method
            )
        else:
            self.assert_no_events_were_emitted()

    def test_no_username(self):
        response = self.post_create_membership(400, {'team_id': self.solar_team.team_id})
        assert 'username' in json.loads(response.content.decode('utf-8'))['field_errors']

    def test_no_team(self):
        response = self.post_create_membership(400, {'username': self.users['student_enrolled_not_on_team'].username})
        assert 'team_id' in json.loads(response.content.decode('utf-8'))['field_errors']

    @ddt.data('staff', 'student_enrolled')
    def test_bad_team(self, user):
        self.post_create_membership(
            404,
            self.build_membership_data_raw(self.users['student_enrolled'].username, 'no_such_team'),
            user=user
        )

    def test_bad_username(self):
        self.post_create_membership(
            404,
            self.build_membership_data_raw('no_such_user', self.solar_team.team_id),
            user='staff'
        )

    @patch('lms.djangoapps.teams.api.is_instructor_managed_team', return_value=True)
    def test_staff_join_instructor_managed_team(self, *args):  # pylint: disable=unused-argument
        self.post_create_membership(
            200,
            self.build_membership_data_raw(self.users['staff'].username, self.solar_team.team_id),
            user='staff'
        )

    @patch('lms.djangoapps.teams.api.is_instructor_managed_team', return_value=True)
    def test_student_join_instructor_managed_team(self, *args):  # pylint: disable=unused-argument
        self.post_create_membership(
            403,
            self.build_membership_data_raw(self.users['student_enrolled_not_on_team'].username, self.solar_team.team_id)
        )

    @ddt.data(
        ('student_masters', 400, 'is already a member'),
        ('student_masters_not_on_team', 200, None),
        ('student_unenrolled', 404, None),
        ('student_enrolled', 404, None),
        ('student_enrolled_both_courses_other_team', 404, None),
        ('staff', 200, None),
    )
    @ddt.unpack
    def test_join_organization_protected_team(self, user, expected_status, expected_message):
        """
        As different users, attempt to join masters_only team.
        Only staff or users within the organization_protected bubble should be able to join the team.
        Anyone else should not be able to join or even tell that the team exists.
        """
        response = self.post_create_membership(
            expected_status,
            self.build_membership_data_raw(self.users[user].username, self.masters_only_team.team_id),
            user=user
        )
        if expected_message:
            assert expected_message in json.loads(response.content.decode('utf-8'))['developer_message']

    @ddt.unpack
    @ddt.data(
        ('student_on_team_1_private_set_1', 403),
        ('student_unenrolled', 404),
        ('student_enrolled', 404),
        ('student_on_team_2_private_set_1', 404),
        ('student_masters', 404),
        ('staff', 200)
    )
    def test_student_join_private_managed_team(self, user, expected_status):
        """
        As different users, attempt to join private_managed team.
        Only staff should be able to add users to any managed teams.
        Anyone else should not be able to join, and only student_on_team_1_private_set_1 should
        be able to tell that the team exists at all.
        (A nonexistant team results in a 404)
        """
        self.post_create_membership(
            expected_status,
            self.build_membership_data_raw(self.users[user].username, self.team_1_in_private_teamset_1.team_id),
            user=user
        )

    @ddt.data('student_enrolled', 'staff', 'course_staff')
    def test_join_twice(self, user):
        response = self.post_create_membership(
            400,
            self.build_membership_data('student_enrolled', self.solar_team),
            user=user
        )
        assert 'already a member' in json.loads(response.content.decode('utf-8'))['developer_message']

    def test_join_second_team_in_course(self):
        """
        Behavior allows the same student to be enrolled in multiple teams, as long as they belong to different
        topics (teamsets)
        """
        self.post_create_membership(
            200,
            self.build_membership_data('student_enrolled_both_courses_other_team', self.solar_team),
            user='student_enrolled_both_courses_other_team'
        )

    @ddt.data('staff', 'course_staff')
    def test_not_enrolled_in_team_course(self, user):
        response = self.post_create_membership(
            400,
            self.build_membership_data('student_unenrolled', self.solar_team),
            user=user
        )
        assert 'not enrolled' in json.loads(response.content.decode('utf-8'))['developer_message']

    def test_over_max_team_size_in_course_2(self):
        response = self.post_create_membership(
            400,
            self.build_membership_data('student_enrolled_other_course_not_on_team', self.another_team),
            user='student_enrolled_other_course_not_on_team'
        )
        assert 'full' in json.loads(response.content.decode('utf-8'))['developer_message']


@ddt.ddt
class TestDetailMembershipAPI(TeamAPITestCase):
    """Test cases for the membership detail endpoint."""

    @ddt.data(
        (None, 401),
        ('student_inactive', 404),
        ('student_unenrolled', 404),
        ('student_enrolled_not_on_team', 200),
        ('student_enrolled', 200),
        ('student_enrolled_inactive', 200),
        ('staff', 200),
        ('course_staff', 200),
        ('community_ta', 200),
    )
    @ddt.unpack
    def test_access(self, user, status):
        self.get_membership_detail(
            self.solar_team.team_id,
            self.users['student_enrolled'].username,
            status,
            user=user
        )

    def test_bad_team(self):
        self.get_membership_detail('no_such_team', self.users['student_enrolled'].username, 404)

    def test_bad_username(self):
        self.get_membership_detail(self.solar_team.team_id, 'no_such_user', 404)

    def test_no_membership(self):
        self.get_membership_detail(
            self.solar_team.team_id,
            self.users['student_enrolled_not_on_team'].username,
            404
        )

    def test_expand_private_user(self):
        # Use the default user which is already private because to year_of_birth is set
        result = self.get_membership_detail(
            self.solar_team.team_id,
            self.users['student_enrolled'].username,
            200,
            {'expand': 'user'}
        )
        self.verify_expanded_private_user(result['user'])

    def test_expand_public_user(self):
        result = self.get_membership_detail(
            self.public_profile_team.team_id,
            self.users['student_enrolled_public_profile'].username,
            200,
            {'expand': 'user'},
            user='student_enrolled_public_profile'
        )
        self.verify_expanded_public_user(result['user'])

    def test_expand_team(self):
        result = self.get_membership_detail(
            self.solar_team.team_id,
            self.users['student_enrolled'].username,
            200,
            {'expand': 'team'}
        )
        self.verify_expanded_team(result['team'])

    @ddt.data(
        ('student_masters', 200),
        ('student_masters_not_on_team', 200),
        ('student_unenrolled', 404),
        ('student_enrolled', 404),
        ('student_enrolled_both_courses_other_team', 404),
        ('staff', 200),
    )
    @ddt.unpack
    def test_organization_protected(self, user, expected_status):
        """
        Users should not be able to see memberships for users in a different bubble than them
        """
        self.get_membership_detail(
            self.masters_only_team.team_id,
            self.users['student_masters'].username,
            expected_status,
            user=user
        )

    @ddt.unpack
    @ddt.data(
        ('student_on_team_1_private_set_1', 200),
        ('student_unenrolled', 404),
        ('student_enrolled', 404),
        ('student_on_team_2_private_set_1', 404),
        ('staff', 200)
    )
    def test_private_managed_team(self, user, expected_status):
        """
        Users should not be able to see memberships for users in private_managed
        teams that they are not a member of
        """
        self.get_membership_detail(
            self.team_1_in_private_teamset_1.team_id,
            self.users['student_on_team_1_private_set_1'].username,
            expected_status,
            user=user
        )

    def test_join_private_managed_teamset(self):
        """
        A user who is not on a private team requests membership info about that team.
        They are added to the team and then try again.
        """
        self.get_membership_detail(
            self.team_1_in_private_teamset_1.team_id,
            self.users['student_on_team_1_private_set_1'].username,
            404,
            user='student_masters'
        )
        self.team_1_in_private_teamset_1.add_user(self.users['student_masters'])
        self.get_membership_detail(
            self.team_1_in_private_teamset_1.team_id,
            self.users['student_on_team_1_private_set_1'].username,
            200,
            user='student_masters'
        )


@ddt.ddt
class TestDeleteMembershipAPI(EventTestMixin, TeamAPITestCase):
    """Test cases for the membership deletion endpoint."""

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp('lms.djangoapps.teams.utils.tracker')

    @ddt.data(
        (None, 401),
        ('student_inactive', 404),
        ('student_unenrolled', 404),
        ('student_enrolled_not_on_team', 404),
        ('student_enrolled', 204),
        ('student_enrolled_inactive', 404),
        ('staff', 204),
        ('course_staff', 204),
        ('community_ta', 204),
    )
    @ddt.unpack
    def test_access(self, user, status):
        self.delete_membership(
            self.solar_team.team_id,
            self.users['student_enrolled'].username,
            status,
            user=user
        )

        if status == 204:
            self.assert_event_emitted(
                'edx.team.learner_removed',
                team_id=self.solar_team.team_id,
                user_id=self.users['student_enrolled'].id,
                remove_method='removed_by_admin'
            )
        else:
            self.assert_no_events_were_emitted()

    def test_leave_team(self):
        """
        The key difference between this test and test_access above is that
        removal via "Edit Membership" and "Leave Team" emit different events
        despite hitting the same API endpoint, due to the 'admin' query string.
        """
        url = reverse('team_membership_detail', args=[self.solar_team.team_id, self.users['student_enrolled'].username])
        self.make_call(url, 204, 'delete', user='student_enrolled')
        self.assert_event_emitted(
            'edx.team.learner_removed',
            team_id=self.solar_team.team_id,
            user_id=self.users['student_enrolled'].id,
            remove_method='self_removal'
        )

    def test_bad_team(self):
        self.delete_membership('no_such_team', self.users['student_enrolled'].username, 404)

    def test_bad_username(self):
        self.delete_membership(self.solar_team.team_id, 'no_such_user', 404)

    def test_missing_membership(self):
        self.delete_membership(self.wind_team.team_id, self.users['student_enrolled'].username, 404)

    @patch('lms.djangoapps.teams.api.is_instructor_managed_team', return_value=True)
    def test_student_leave_instructor_managed_team(self, *args):  # pylint: disable=unused-argument
        self.delete_membership(
            self.solar_team.team_id, self.users['student_enrolled'].username, 403, user='student_enrolled')

    @ddt.unpack
    @ddt.data(
        ('student_enrolled', 'student_masters', 404),
        ('student_enrolled', 'student_enrolled', 404),
        ('student_masters_not_on_team', 'student_masters', 404),
        ('student_masters_not_on_team', 'student_masters_not_on_team', 404),
        ('student_masters', 'student_masters', 204),
        ('staff', 'student_masters', 204),
        ('staff', 'staff', 404),
    )
    def test_organization_protection_status(self, user, user_to_remove, expected_status):
        """
        As different users, attempt to remove themselves or studet_masters from masters_only team.
        (only student_masters is actually on this team)
        Only staff and the student_masters should be able to remove, and users outside of the
        organization_protected bubble should not be able to tell that the team exists in
        any way.

        TODO: student_enrolled should not be able to tell that masters_only team exists, he should
        get a 404 on both calls
        """
        self.delete_membership(
            self.masters_only_team.team_id,
            self.users[user_to_remove].username,
            expected_status,
            user=user
        )

    @ddt.unpack
    @ddt.data(
        ('student_enrolled', 'student_on_team_1_private_set_1', 404),
        ('student_enrolled', 'student_enrolled', 404),
        ('student_on_team_1_private_set_1', 'student_on_team_1_private_set_1', 403),
        ('student_on_team_2_private_set_1', 'student_on_team_1_private_set_1', 404),
        ('student_on_team_2_private_set_1', 'student_on_team_2_private_set_1', 404),
        ('staff', 'student_on_team_1_private_set_1', 204),
        ('staff', 'staff', 404),
    )
    def test_remove_user_from_private_teamset(self, user, user_to_remove, expected_status):
        """
        As different users, attempt to remove themselves or student_on_team_1_private_set_1 from a
        private_managed team.
        (only student_on_team_1_private_set_1 is actually on this team)
        Only staff should be able to remove, and all users other than student_on_team_1_private_set_1
        should not be able to tell that the team exists in any way.

        TODO: The only 403 that should remain is student_on_team_1_private_set_1. The other users should not be
        able to tell that the team exists.
        """
        self.delete_membership(
            self.team_1_in_private_teamset_1.team_id,
            self.users[user_to_remove].username,
            expected_status,
            user=user
        )


class TestElasticSearchErrors(TeamAPITestCase):
    """Test that the Team API is robust to Elasticsearch connection errors."""

    ES_ERROR = ConnectionError('N/A', 'connection error', {})

    @patch.object(SearchEngine, 'get_search_engine', side_effect=ES_ERROR)
    def test_list_teams(self, __):
        """Test that text searches return a 503 when Elasticsearch is down.

        The endpoint should still return 200 when a search is not supplied."""
        self.get_teams_list(
            expected_status=503,
            data={'course_id': str(self.test_course_1.id), 'text_search': 'zoinks'},
            user='staff'
        )
        self.get_teams_list(
            expected_status=200,
            data={'course_id': str(self.test_course_1.id)},
            user='staff'
        )

    @patch.object(SearchEngine, 'get_search_engine', side_effect=ES_ERROR)
    def test_create_team(self, __):
        """Test that team creation is robust to Elasticsearch errors."""
        self.post_create_team(
            expected_status=200,
            data=self.build_team_data(name='zoinks'),
            user='staff'
        )

    @patch.object(SearchEngine, 'get_search_engine', side_effect=ES_ERROR)
    def test_delete_team(self, __):
        """Test that team deletion is robust to Elasticsearch errors."""
        self.delete_team(self.wind_team.team_id, 204, user='staff')

    @patch.object(SearchEngine, 'get_search_engine', side_effect=ES_ERROR)
    def test_patch_team(self, __):
        """Test that team updates are robust to Elasticsearch errors."""
        self.patch_team_detail(
            self.wind_team.team_id,
            200,
            data={'description': 'new description'},
            user='staff'
        )


@ddt.ddt
class TestBulkMembershipManagement(TeamAPITestCase):
    """
    Test that CSVs can be uploaded and downloaded to manage course membership.

    This test case will be expanded when the view is fully
    implemented (TODO MST-31).
    """
    good_course_id = 'course-v1:TestX+TS101+Test_Course'
    fake_course_id = 'course-v1:TestX+TS101+Non_Existent_Course'

    allow_username = 'course_staff'
    deny_username = 'student_enrolled'

    @ddt.data(
        ('GET', good_course_id, deny_username, 403),
        ('GET', fake_course_id, allow_username, 404),
        ('GET', fake_course_id, deny_username, 404),
        ('POST', good_course_id, deny_username, 403),
    )
    @ddt.unpack
    def test_error_statuses(self, method, course_id, username, expected_status):
        url = self.get_url(course_id)
        self.login(username)
        response = self.client.generic(method, url)
        assert response.status_code == expected_status

    def test_download_csv(self):
        url = self.get_url(self.good_course_id)
        self.login(self.allow_username)
        response = self.client.get(url)
        assert response.status_code == 200
        assert response['Content-Type'] == 'text/csv'
        assert response['Content-Disposition'] == (
            'attachment; filename="team-membership_TestX_TS101_Test_Course.csv"'
        )
        # For now, just assert that the file is non-empty.
        # Eventually, we will test contents (TODO MST-31).
        assert response.content

    @staticmethod
    def get_url(course_id):
        # This strategy allows us to test with invalid course IDs
        return reverse('team_membership_bulk_management', args=[course_id])

    def test_create_membership_via_upload(self):
        self.create_and_enroll_student(username='a_user')
        csv_content = 'username,mode,topic_0\n'
        csv_content += 'a_user,audit,team wind power'
        csv_file = SimpleUploadedFile('test_file.csv', csv_content.encode('utf8'), content_type='text/csv')
        self.client.login(username=self.users['course_staff'].username, password=self.users['course_staff'].password)
        response = self.make_call(
            reverse('team_membership_bulk_management', args=[self.good_course_id]),
            201,
            method='post',
            data={'csv': csv_file},
            user='staff'
        )
        response_text = json.loads(response.content.decode('utf-8'))
        assert response_text['message'] == '1 learners were affected.'

    def test_upload_invalid_teamset(self):
        self.create_and_enroll_student(username='a_user')
        csv_content = 'username,mode,topic_0_bad\n'
        csv_content += 'a_user,audit,team wind power'
        csv_file = SimpleUploadedFile('test_file.csv', csv_content.encode('utf8'), content_type='text/csv')
        self.client.login(username=self.users['course_staff'].username, password=self.users['course_staff'].password)
        self.make_call(
            reverse('team_membership_bulk_management', args=[self.good_course_id]),
            400,
            method='post',
            data={'csv': csv_file},
            user='staff'
        )

    def test_upload_assign_user_twice_to_same_teamset(self):
        csv_content = 'username,mode,topic_0\n'
        csv_content += 'student_enrolled, masters, team wind power'
        csv_file = SimpleUploadedFile('test_file.csv', csv_content.encode('utf8'), content_type='text/csv')
        self.client.login(username=self.users['course_staff'].username, password=self.users['course_staff'].password)
        self.make_call(
            reverse('team_membership_bulk_management', args=[self.good_course_id]),
            400, method='post',
            data={'csv': csv_file},
            user='staff'
        )

    def test_upload_assign_one_user_to_different_teamsets(self):
        self.create_and_enroll_student(username='a_user')
        self.create_and_enroll_student(username='b_user')
        self.create_and_enroll_student(username='c_user')
        csv_content = 'username,mode,topic_0,topic_1,topic_2\n'
        csv_content += 'a_user,audit,team wind power,team 2\n'
        csv_content += 'b_user,audit,,team 2\n'
        csv_content += 'c_user,audit,,,team 3'
        csv_file = SimpleUploadedFile('test_file.csv', csv_content.encode('utf8'), content_type='text/csv')
        self.client.login(username=self.users['course_staff'].username, password=self.users['course_staff'].password)
        response = self.make_call(
            reverse('team_membership_bulk_management', args=[self.good_course_id]),
            201,
            method='post',
            data={'csv': csv_file},
            user='staff',
        )
        assert CourseTeam.objects.filter(name='team 2', course_id=self.test_course_1.id).count() == 1
        response_text = json.loads(response.content.decode('utf-8'))
        assert response_text['message'] == '3 learners were affected.'

    def test_upload_non_existing_user(self):
        csv_content = 'username,mode,topic_0\n'
        csv_content += 'missing_user, masters, team wind power'
        csv_file = SimpleUploadedFile('test_file.csv', csv_content.encode('utf8'), content_type='text/csv')
        self.client.login(username=self.users['course_staff'].username, password=self.users['course_staff'].password)
        self.make_call(
            reverse('team_membership_bulk_management', args=[self.good_course_id]),
            400,
            method='post',
            data={'csv': csv_file},
            user='staff',
        )

    def test_upload_only_existing_courses(self):
        self.create_and_enroll_student(username='a_user', mode=CourseMode.MASTERS)
        self.create_and_enroll_student(username='b_user', mode=CourseMode.MASTERS)
        existing_team_1 = CourseTeamFactory.create(
            course_id=self.test_course_1.id,
            topic_id='topic_1',
            organization_protected=True
        )
        existing_team_2 = CourseTeamFactory.create(
            course_id=self.test_course_1.id,
            topic_id='topic_2',
            organization_protected=True
        )

        csv_content = 'username,mode,topic_1,topic_2\n'
        csv_content += 'a_user,masters,{},{}\n'.format(
            existing_team_1.name,
            existing_team_2.name
        )
        csv_content += 'b_user,masters,{},{}\n'.format(
            existing_team_1.name,
            existing_team_2.name
        )
        csv_file = SimpleUploadedFile('test_file.csv', csv_content.encode('utf8'), content_type='text/csv')
        self.client.login(username=self.users['course_staff'].username, password=self.users['course_staff'].password)
        self.make_call(
            reverse('team_membership_bulk_management', args=[self.good_course_id]),
            201,
            method='post',
            data={'csv': csv_file},
            user='staff'
        )

    def test_upload_invalid_header(self):
        self.create_and_enroll_student(username='a_user')
        csv_content = 'mode,topic_1\n'
        csv_content += 'a_user,audit, team wind power'
        csv_file = SimpleUploadedFile('test_file.csv', csv_content.encode('utf8'), content_type='text/csv')
        self.client.login(username=self.users['course_staff'].username, password=self.users['course_staff'].password)
        self.make_call(
            reverse('team_membership_bulk_management', args=[self.good_course_id]),
            400, method='post',
            data={'csv': csv_file},
            user='staff',
        )

    def test_upload_invalid_more_teams_than_teamsets(self):
        self.create_and_enroll_student(username='a_user')
        csv_content = 'username,mode,topic_1\n'
        csv_content += 'a_user, masters, team wind power, extra1, extra2'
        csv_file = SimpleUploadedFile('test_file.csv', csv_content.encode('utf8'), content_type='text/csv')
        self.client.login(username=self.users['course_staff'].username, password=self.users['course_staff'].password)
        self.make_call(reverse(
            'team_membership_bulk_management',
            args=[self.good_course_id]),
            400, method='post',
            data={'csv': csv_file},
            user='staff',
        )

    def test_upload_invalid_student_enrollment_mismatch(self):
        self.create_and_enroll_student(username='a_user', mode=CourseMode.AUDIT)
        csv_content = 'username,mode,topic_1\n'
        csv_content += 'a_user,masters,team wind power'
        csv_file = SimpleUploadedFile('test_file.csv', csv_content.encode('utf8'), content_type='text/csv')
        self.client.login(username=self.users['course_staff'].username, password=self.users['course_staff'].password)
        self.make_call(reverse(
            'team_membership_bulk_management',
            args=[self.good_course_id]),
            400, method='post',
            data={'csv': csv_file}, user='staff'
        )

    def test_upload_invalid_multiple_student_enrollment_mismatch(self):
        audit_username = 'audit_user'
        masters_username_a = 'masters_a'
        masters_username_b = 'masters_b'
        self.create_and_enroll_student(username=audit_username, mode=CourseMode.AUDIT)
        self.create_and_enroll_student(username=masters_username_a, mode=CourseMode.MASTERS)
        self.create_and_enroll_student(username=masters_username_b, mode=CourseMode.MASTERS)

        csv_content = 'username,mode,topic_1\n'
        csv_content += f'{audit_username},audit,team wind power\n'
        csv_content += f'{masters_username_a},masters,team wind power\n'
        csv_content += f'{masters_username_b},masters,team wind power\n'
        csv_file = SimpleUploadedFile('test_file.csv', csv_content.encode('utf8'), content_type='text/csv')
        self.client.login(username=self.users['course_staff'].username, password=self.users['course_staff'].password)
        response = self.make_call(reverse(
            'team_membership_bulk_management',
            args=[self.good_course_id]),
            400, method='post',
            data={'csv': csv_file}, user='staff'
        )
        response_text = json.loads(response.content.decode('utf-8'))
        expected_error = 'Team team wind power cannot have Master’s track users mixed with users in other tracks.'
        assert response_text['errors'][0] == expected_error

    def test_upload_learners_exceed_max_team_size(self):
        csv_content = 'username,mode,topic_0,topic_1\n'
        team1 = 'team wind power'
        team2 = 'team 2'
        for name_enum in enumerate(['a', 'b', 'c', 'd', 'e', 'f', 'g']):
            username = f'user_{name_enum[1]}'
            self.create_and_enroll_student(username=username, mode=CourseMode.MASTERS)
            csv_content += f'{username},masters,{team1},{team2}\n'

        csv_file = SimpleUploadedFile('test_file.csv', csv_content.encode('utf8'), content_type='text/csv')
        self.client.login(username=self.users['course_staff'].username, password=self.users['course_staff'].password)
        response = self.make_call(reverse(
            'team_membership_bulk_management',
            args=[self.good_course_id]),
            400, method='post',
            data={'csv': csv_file}, user='staff'
        )
        response_text = json.loads(response.content.decode('utf-8'))
        assert response_text['errors'][0] == f'New membership for team {team1} would exceed max size of {3}.'

    def test_deletion_via_upload_csv(self):
        # create a team membership that will be used further down
        self.test_create_membership_via_upload()
        username = 'a_user'
        topic_0_id = 'topic_0'
        assert CourseTeamMembership.objects.filter(user_id=self.users[username].id, team__topic_id=topic_0_id).exists()

        csv_content = f'username,mode,{topic_0_id},topic_1\n'
        csv_content += f'{username},audit'
        csv_file = SimpleUploadedFile('test_file.csv', csv_content.encode('utf8'), content_type='text/csv')
        self.client.login(username=self.users['course_staff'].username, password=self.users['course_staff'].password)
        self.make_call(
            reverse('team_membership_bulk_management', args=[self.good_course_id]),
            201,
            method='post',
            data={'csv': csv_file},
            user='staff'
        )
        assert not CourseTeamMembership.objects \
            .filter(user_id=self.users[username].id, team__topic_id=topic_0_id).exists()

    def test_reassignment_via_upload_csv(self):
        # create a team membership that will be used further down
        self.test_create_membership_via_upload()
        username = 'a_user'
        topic_0_id = 'topic_0'
        nuclear_team_name = 'team nuclear power'
        windpower_team_name = 'team wind power'
        assert CourseTeamMembership.objects \
            .filter(user_id=self.users[username].id, team__topic_id=topic_0_id, team__name=windpower_team_name).exists()
        csv_content = f'username,mode,{topic_0_id}\n'
        csv_content += f'{username},audit,{nuclear_team_name}'
        csv_file = SimpleUploadedFile('test_file.csv', csv_content.encode('utf8'), content_type='text/csv')
        self.client.login(username=self.users['course_staff'].username, password=self.users['course_staff'].password)
        self.make_call(
            reverse('team_membership_bulk_management', args=[self.good_course_id]),
            201,
            method='post',
            data={'csv': csv_file},
            user='staff'
        )
        assert not CourseTeamMembership.objects.filter(
            user_id=self.users[username].id,
            team__topic_id=topic_0_id,
            team__name=windpower_team_name,
        ).exists()

        assert CourseTeamMembership.objects.filter(
            user_id=self.users[username].id,
            team__topic_id=topic_0_id,
            team__name=nuclear_team_name,
        ).exists()

    def test_upload_file_not_changed_csv(self):
        # create a team membership that will be used further down
        self.test_create_membership_via_upload()
        username = 'a_user'
        topic_0_id = 'topic_0'
        nuclear_team_name = 'team wind power'
        assert len(CourseTeamMembership.objects.filter(user_id=self.users[username].id, team__topic_id=topic_0_id)) == 1
        csv_content = f'username,mode,{topic_0_id}\n'
        csv_content += f'{username},audit,{nuclear_team_name}'
        csv_file = SimpleUploadedFile('test_file.csv', csv_content.encode('utf8'), content_type='text/csv')
        self.client.login(
            username=self.users['course_staff'].username,
            password=self.users['course_staff'].password,
        )
        self.make_call(
            reverse('team_membership_bulk_management', args=[self.good_course_id]),
            201,
            method='post',
            data={'csv': csv_file},
            user='staff'
        )
        assert len(
            CourseTeamMembership.objects.filter(user_id=self.users[username].id, team__name=nuclear_team_name)
        ) == 1
        assert CourseTeamMembership.objects.filter(
            user_id=self.users[username].id,
            team__name=nuclear_team_name,
        ).exists()

    def test_create_membership_via_upload_using_external_key(self):
        self.create_and_enroll_student(username='a_user', external_key='a_user_external_key')
        csv_content = 'username,external_user_id,mode,topic_0\n'
        csv_content += 'a_user,a_user_external_key,audit,team wind power'
        csv_file = SimpleUploadedFile('test_file.csv', csv_content.encode('utf8'), content_type='text/csv')
        self.client.login(username=self.users['course_staff'].username, password=self.users['course_staff'].password)
        response = self.make_call(
            reverse('team_membership_bulk_management', args=[self.good_course_id]),
            201,
            method='post',
            data={'csv': csv_file},
            user='staff'
        )
        response_text = json.loads(response.content.decode('utf-8'))
        assert response_text['message'] == '1 learners were affected.'

    @unittest.skip("This currently won't fail since we're only using username")
    def test_create_membership_via_upload_using_external_key_invalid(self):
        self.create_and_enroll_student(username='a_user', external_key='a_user_external_key')
        csv_content = 'username,external_user_id,mode,topic_0\n'
        csv_content += 'a_user,a_user_external_key_invalid,audit,team wind power'
        csv_file = SimpleUploadedFile('test_file.csv', csv_content.encode('utf8'), content_type='text/csv')
        self.client.login(username=self.users['course_staff'].username, password=self.users['course_staff'].password)
        response = self.make_call(
            reverse('team_membership_bulk_management', args=[self.good_course_id]),
            400,
            method='post',
            data={'csv': csv_file},
            user='staff'
        )
        response_text = json.loads(response.content.decode('utf-8'))
        assert response_text['errors'] == ['User name/email/external key: a_user_external_key_invalid does not exist.']

    def test_upload_non_ascii(self):
        csv_content = 'username,mode,topic_0\n'
        team_name = '著文企臺個'
        user_name = '著著文企臺個文企臺個'
        self.create_and_enroll_student(username=user_name)
        csv_content += f'{user_name},audit,{team_name}'
        csv_file = SimpleUploadedFile('test_file.csv', csv_content.encode('utf8'), content_type='text/csv')
        self.client.login(username=self.users['course_staff'].username, password=self.users['course_staff'].password)
        self.make_call(
            reverse('team_membership_bulk_management', args=[self.good_course_id]),
            201, method='post',
            data={'csv': csv_file},
            user='staff'
        )
        team = self.users[user_name].teams.first()
        assert team.name == team_name
        assert [user.username for user in team.users.all()] == [user_name]

    def test_upload_assign_masters_learner_to_non_protected_team(self):
        """
        Scenario: Attempt to add a learner enrolled in masters track to an existing, non-org protected team.
        Outcome: Must fail
        """
        masters_a = 'masters_a'
        team = self.wind_team
        self.create_and_enroll_student(username=masters_a, mode=CourseMode.MASTERS)
        csv_content = f'username,mode,{team.topic_id}\n'
        csv_content += f'masters_a, masters,{team.name}'
        csv_file = SimpleUploadedFile('test_file.csv', csv_content.encode('utf8'), content_type='text/csv')
        self.client.login(username=self.users['course_staff'].username, password=self.users['course_staff'].password)

        response = self.make_call(
            reverse('team_membership_bulk_management', args=[self.good_course_id]),
            400, method='post',
            data={'csv': csv_file},
            user='staff'
        )
        response_text = json.loads(response.content.decode('utf-8'))
        expected_message = 'Team {} cannot have Master’s track users mixed with users in other tracks.'.format(
            team.name
        )
        assert response_text['errors'][0] == expected_message
