"""
Tests for the Bulk Enrollment views.
"""


import json

import ddt
from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core import mail
from django.test.utils import override_settings
from django.urls import reverse
from opaque_keys.edx.keys import CourseKey
from rest_framework.test import APIRequestFactory, APITestCase, force_authenticate

from common.djangoapps.student.models import (  # lint-amnesty, pylint: disable=line-too-long
    ENROLLED_TO_ENROLLED,
    ENROLLED_TO_UNENROLLED,
    UNENROLLED_TO_ENROLLED,
    CourseEnrollment,
    ManualEnrollmentAudit
)
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.bulk_enroll.serializers import BulkEnrollmentSerializer
from lms.djangoapps.bulk_enroll.views import BulkEnrollView
from lms.djangoapps.courseware.tests.helpers import LoginEnrollmentTestCase
from openedx.core.djangoapps.course_groups.cohorts import get_cohort_id
from openedx.core.djangoapps.course_groups.tests.helpers import config_course_cohorts
from openedx.core.djangoapps.site_configuration.helpers import get_value as get_site_value
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@override_settings(ENABLE_BULK_ENROLLMENT_VIEW=True)
@ddt.ddt
class BulkEnrollmentTest(ModuleStoreTestCase, LoginEnrollmentTestCase, APITestCase):
    """
    Test the bulk enrollment endpoint
    """

    USERNAME = "Bob"
    EMAIL = "bob@example.com"
    PASSWORD = "edx"

    def setUp(self):
        """ Create a course and user, then log in. """
        super().setUp()

        self.view = BulkEnrollView.as_view()
        self.request_factory = APIRequestFactory()
        self.url = reverse('bulk_enroll')

        self.staff = UserFactory.create(
            username=self.USERNAME,
            email=self.EMAIL,
            password=self.PASSWORD,
            is_staff=True,
        )

        self.course = CourseFactory.create()
        self.course_key = str(self.course.id)
        self.enrolled_student = UserFactory(username='EnrolledStudent', first_name='Enrolled', last_name='Student')
        CourseEnrollment.enroll(
            self.enrolled_student,
            self.course.id
        )
        self.notenrolled_student = UserFactory(username='NotEnrolledStudent', first_name='NotEnrolled',
                                               last_name='Student')

        # Email URL values
        self.site_name = get_site_value(
            'SITE_NAME',
            settings.SITE_NAME
        )
        self.about_path = f'/courses/{self.course.id}/about'
        self.course_path = f'/courses/{self.course.id}/'

    def request_bulk_enroll(self, data=None, use_json=False, **extra):
        """ Make an authenticated request to the bulk enrollment API. """
        content_type = None
        if use_json:
            content_type = 'application/json'
            data = json.dumps(data)
        request = self.request_factory.post(self.url, data=data, content_type=content_type, **extra)
        force_authenticate(request, user=self.staff)
        response = self.view(request)
        response.render()
        return response

    def test_course_list_serializer(self):
        """
        Test that the course serializer will work when passed a string or list.

        Internally, DRF passes the data into the value conversion method as a list instead of
        a string, so StringListField needs to work with both.
        """
        for key in [self.course_key, [self.course_key]]:
            serializer = BulkEnrollmentSerializer(data={
                'identifiers': 'percivaloctavius',
                'action': 'enroll',
                'email_students': False,
                'courses': key,
            })
            assert serializer.is_valid()

    def test_non_staff(self):
        """ Test that non global staff users are forbidden from API use. """
        self.staff.is_staff = False
        self.staff.save()
        with self.allow_transaction_exception():
            response = self.request_bulk_enroll()
        assert response.status_code == 403

    def test_missing_params(self):
        """ Test the response when missing all query parameters. """
        response = self.request_bulk_enroll()
        assert response.status_code == 400

    def test_bad_action(self):
        """ Test the response given an invalid action """
        response = self.request_bulk_enroll({
            'identifiers': self.enrolled_student.email,
            'action': 'invalid-action',
            'courses': self.course_key,
        })
        assert response.status_code == 400

    def test_invalid_email(self):
        """ Test the response given an invalid email. """
        response = self.request_bulk_enroll({
            'identifiers': 'percivaloctavius@',
            'action': 'enroll',
            'email_students': False,
            'courses': self.course_key,
        })
        assert response.status_code == 200

        # test the response data
        expected = {
            "action": "enroll",
            'auto_enroll': False,
            'email_students': False,
            "courses": {
                self.course_key: {
                    "action": "enroll",
                    'auto_enroll': False,
                    "results": [
                        {
                            "identifier": 'percivaloctavius@',
                            "invalidIdentifier": True,
                            "success": False,
                            "error_type": "invalid_identifier",
                            "error_message": "Invalid email address",
                        }
                    ]
                }
            }
        }

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

    def test_invalid_username(self):
        """ Test the response given an invalid username. """
        response = self.request_bulk_enroll({
            'identifiers': 'percivaloctavius',
            'action': 'enroll',
            'email_students': False,
            'courses': self.course_key,
        })
        assert response.status_code == 200

        # test the response data
        expected = {
            "action": "enroll",
            'auto_enroll': False,
            'email_students': False,
            "courses": {
                self.course_key: {
                    "action": "enroll",
                    'auto_enroll': False,
                    "results": [
                        {
                            "identifier": 'percivaloctavius',
                            "invalidIdentifier": True,
                            "success": False,
                            "error_type": "invalid_identifier",
                            "error_message": "Invalid email address",
                        }
                    ]
                }
            }
        }

        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

    def test_enroll_with_username(self):
        """ Test enrolling using a username as the identifier. """
        response = self.request_bulk_enroll({
            'identifiers': self.notenrolled_student.username,
            'action': 'enroll',
            'email_students': False,
            'courses': self.course_key,
        })
        assert response.status_code == 200

        # test the response data
        expected = {
            "action": "enroll",
            'auto_enroll': False,
            "email_students": False,
            "courses": {
                self.course_key: {
                    "action": "enroll",
                    'auto_enroll': False,
                    "results": [
                        {
                            "identifier": self.notenrolled_student.username,
                            "before": {
                                "enrollment": False,
                                "auto_enroll": False,
                                "user": True,
                                "allowed": False,
                            },
                            "after": {
                                "enrollment": True,
                                "auto_enroll": False,
                                "user": True,
                                "allowed": False,
                            },
                            "success": True,
                            "state_transition": UNENROLLED_TO_ENROLLED,
                        }
                    ]
                }
            }
        }
        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition == UNENROLLED_TO_ENROLLED
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

    @ddt.data(False, True)
    def test_enroll_with_email(self, use_json):
        """ Test enrolling using a username as the identifier. """
        response = self.request_bulk_enroll({
            'identifiers': self.notenrolled_student.email,
            'action': 'enroll',
            'email_students': False,
            'courses': self.course_key,
        }, use_json=use_json)
        assert response.status_code == 200

        # test that the user is now enrolled
        user = User.objects.get(email=self.notenrolled_student.email)
        assert CourseEnrollment.is_enrolled(user, self.course.id)

        # test the response data
        expected = {
            "action": "enroll",
            "auto_enroll": False,
            "email_students": False,
            "courses": {
                self.course_key: {
                    "action": "enroll",
                    "auto_enroll": False,
                    "results": [
                        {
                            "identifier": self.notenrolled_student.email,
                            "before": {
                                "enrollment": False,
                                "auto_enroll": False,
                                "user": True,
                                "allowed": False,
                            },
                            "after": {
                                "enrollment": True,
                                "auto_enroll": False,
                                "user": True,
                                "allowed": False,
                            },
                            "success": True,
                            "state_transition": UNENROLLED_TO_ENROLLED,
                        }
                    ]
                }
            }
        }

        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition == UNENROLLED_TO_ENROLLED
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

        # Check the outbox
        assert len(mail.outbox) == 0

    @ddt.data(False, True)
    def test_unenroll(self, use_json):
        """ Test unenrolling a user. """
        response = self.request_bulk_enroll({
            'identifiers': self.enrolled_student.email,
            'action': 'unenroll',
            'email_students': False,
            'courses': self.course_key,
        }, use_json=use_json)
        assert response.status_code == 200

        # test that the user is now unenrolled
        user = User.objects.get(email=self.enrolled_student.email)
        assert not CourseEnrollment.is_enrolled(user, self.course.id)

        # test the response data
        expected = {
            "action": "unenroll",
            "auto_enroll": False,
            "email_students": False,
            "courses": {
                self.course_key: {
                    "action": "unenroll",
                    "auto_enroll": False,
                    "results": [
                        {
                            "identifier": self.enrolled_student.email,
                            "before": {
                                "enrollment": True,
                                "auto_enroll": False,
                                "user": True,
                                "allowed": False,
                            },
                            "after": {
                                "enrollment": False,
                                "auto_enroll": False,
                                "user": True,
                                "allowed": False,
                            },
                            "success": True,
                            "state_transition": ENROLLED_TO_UNENROLLED,
                        }
                    ]
                }
            }

        }

        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition == ENROLLED_TO_UNENROLLED
        res_json = json.loads(response.content.decode('utf-8'))
        assert res_json == expected

        # Check the outbox
        assert len(mail.outbox) == 0

    def test_fail_on_unequal_cohorts(self):
        """
        Test unequal items in cohorts and courses.
        """
        response = self.request_bulk_enroll({
            'identifiers': self.notenrolled_student.username,
            'action': 'enroll',
            'email_students': False,
            'courses': self.course_key,
            'cohorts': "cohort1,cohort2"
        })
        self.assertContains(
            response,
            'If provided, the cohorts and courses should have equal number of items.',
            status_code=400,
        )

    def test_fail_on_missing_cohorts(self):
        """
        Test cohorts don't exist in the course.
        """
        response = self.request_bulk_enroll({
            'identifiers': self.notenrolled_student.username,
            'action': 'enroll',
            'email_students': False,
            'cohorts': 'cohort1',
            'courses': self.course_key
        })
        self.assertContains(
            response,
            'cohort {cohort_name} not found in course {course_id}.'.format(
                cohort_name='cohort1', course_id=self.course_key
            ),
            status_code=400,
        )

    def test_allow_cohorts_when_enrolling(self):
        """
        Test if the cohorts are given but the action is unenroll.
        """
        config_course_cohorts(self.course, is_cohorted=True, manual_cohorts=["cohort1", "cohort2"])
        response = self.request_bulk_enroll({
            'identifiers': self.notenrolled_student.username,
            'action': 'unenroll',
            'email_students': False,
            'cohorts': 'cohort1',
            'courses': self.course_key
        })
        self.assertContains(response, 'Cohorts can only be used for enrollments.', status_code=400)

    def test_add_to_valid_cohort(self):
        config_course_cohorts(self.course, is_cohorted=True, manual_cohorts=["cohort1", "cohort2"])
        response = self.request_bulk_enroll({
            'identifiers': self.notenrolled_student.username,
            'action': 'enroll',
            'email_students': False,
            'courses': self.course_key,
            'cohorts': "cohort1"
        })

        assert response.status_code == 200

        # test the response data
        expected = {
            "action": "enroll",
            'auto_enroll': False,
            "email_students": False,
            "courses": {
                self.course_key: {
                    "action": "enroll",
                    'auto_enroll': False,
                    "results": [
                        {
                            "identifier": self.notenrolled_student.username,
                            "before": {
                                "enrollment": False,
                                "auto_enroll": False,
                                "user": True,
                                "allowed": False,
                                "cohort": None,
                            },
                            "after": {
                                "enrollment": True,
                                "auto_enroll": False,
                                "user": True,
                                "allowed": False,
                                "cohort": 'cohort1',
                            },
                            "success": True,
                            "state_transition": UNENROLLED_TO_ENROLLED,
                        }
                    ]
                }
            }
        }
        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition == UNENROLLED_TO_ENROLLED
        res_json = json.loads(response.content.decode('utf-8'))
        assert get_cohort_id(self.notenrolled_student, CourseKey.from_string(self.course_key)) is not None

        assert res_json == expected

    def test_readd_to_different_cohort(self):
        config_course_cohorts(self.course, is_cohorted=True, manual_cohorts=["cohort1", "cohort2"])
        response = self.request_bulk_enroll({
            'identifiers': self.notenrolled_student.username,
            'action': 'enroll',
            'email_students': False,
            'courses': self.course_key,
            'cohorts': "cohort1"
        })

        assert response.status_code == 200

        # test the response data
        expected = {
            "action": "enroll",
            'auto_enroll': False,
            "email_students": False,
            "courses": {
                self.course_key: {
                    "action": "enroll",
                    'auto_enroll': False,
                    "results": [
                        {
                            "identifier": self.notenrolled_student.username,
                            "before": {
                                "enrollment": False,
                                "auto_enroll": False,
                                "user": True,
                                "allowed": False,
                                "cohort": None,
                            },
                            "after": {
                                "enrollment": True,
                                "auto_enroll": False,
                                "user": True,
                                "allowed": False,
                                "cohort": 'cohort1',
                            },
                            "success": True,
                            "state_transition": UNENROLLED_TO_ENROLLED,
                        }
                    ]
                }
            }
        }
        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition == UNENROLLED_TO_ENROLLED
        res_json = json.loads(response.content.decode('utf-8'))
        assert get_cohort_id(self.notenrolled_student, CourseKey.from_string(self.course_key)) is not None
        assert res_json == expected

        response2 = self.request_bulk_enroll({
            'identifiers': self.notenrolled_student.username,
            'action': 'enroll',
            'email_students': False,
            'courses': self.course_key,
            'cohorts': "cohort2"
        })

        assert response2.status_code == 200

        # test the response data
        expected2 = {
            "action": "enroll",
            'auto_enroll': False,
            "email_students": False,
            "courses": {
                self.course_key: {
                    "action": "enroll",
                    'auto_enroll': False,
                    "results": [
                        {
                            "identifier": self.notenrolled_student.username,
                            "before": {
                                "enrollment": True,
                                "auto_enroll": False,
                                "user": True,
                                "allowed": False,
                                "cohort": 'cohort1',
                            },
                            "after": {
                                "enrollment": True,
                                "auto_enroll": False,
                                "user": True,
                                "allowed": False,
                                "cohort": 'cohort2',
                            },
                            "success": True,
                            "state_transition": ENROLLED_TO_ENROLLED,
                        }
                    ]
                }
            }
        }
        res2_json = json.loads(response2.content.decode('utf-8'))
        assert get_cohort_id(self.notenrolled_student, CourseKey.from_string(self.course_key)) is not None
        assert res2_json == expected2

    def test_readd_to_same_cohort(self):
        config_course_cohorts(self.course, is_cohorted=True, manual_cohorts=["cohort1", "cohort2"])
        response = self.request_bulk_enroll({
            'identifiers': self.notenrolled_student.username,
            'action': 'enroll',
            'email_students': False,
            'courses': self.course_key,
            'cohorts': "cohort1"
        })

        assert response.status_code == 200

        # test the response data
        expected = {
            "action": "enroll",
            'auto_enroll': False,
            "email_students": False,
            "courses": {
                self.course_key: {
                    "action": "enroll",
                    'auto_enroll': False,
                    "results": [
                        {
                            "identifier": self.notenrolled_student.username,
                            "before": {
                                "enrollment": False,
                                "auto_enroll": False,
                                "user": True,
                                "allowed": False,
                                "cohort": None,
                            },
                            "after": {
                                "enrollment": True,
                                "auto_enroll": False,
                                "user": True,
                                "allowed": False,
                                "cohort": 'cohort1',
                            },
                            "success": True,
                            "state_transition": UNENROLLED_TO_ENROLLED,
                        }
                    ]
                }
            }
        }
        manual_enrollments = ManualEnrollmentAudit.objects.all()
        assert manual_enrollments.count() == 1
        assert manual_enrollments[0].state_transition == UNENROLLED_TO_ENROLLED
        res_json = json.loads(response.content.decode('utf-8'))
        assert get_cohort_id(self.notenrolled_student, CourseKey.from_string(self.course_key)) is not None

        assert res_json == expected

        response2 = self.request_bulk_enroll({
            'identifiers': self.notenrolled_student.username,
            'action': 'enroll',
            'email_students': False,
            'courses': self.course_key,
            'cohorts': "cohort1"
        })

        assert response2.status_code == 200

        # test the response data
        expected2 = {
            "action": "enroll",
            'auto_enroll': False,
            "email_students": False,
            "courses": {
                self.course_key: {
                    "action": "enroll",
                    'auto_enroll': False,
                    "results": [
                        {
                            "identifier": self.notenrolled_student.username,
                            "before": {
                                "enrollment": True,
                                "auto_enroll": False,
                                "user": True,
                                "allowed": False,
                                "cohort": 'cohort1',
                            },
                            "after": {
                                "enrollment": True,
                                "auto_enroll": False,
                                "user": True,
                                "allowed": False,
                                "cohort": 'cohort1',
                            },
                            "success": True,
                            "state_transition": ENROLLED_TO_ENROLLED,
                        }
                    ]
                }
            }
        }
        res2_json = json.loads(response2.content.decode('utf-8'))
        assert get_cohort_id(self.notenrolled_student, CourseKey.from_string(self.course_key)) is not None
        assert res2_json == expected2
