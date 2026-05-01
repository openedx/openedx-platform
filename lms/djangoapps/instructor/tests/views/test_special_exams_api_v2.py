"""
Tests for Instructor API v2 Special Exams endpoints.
"""
from unittest.mock import patch

import ddt
from django.conf import settings
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone
from edx_proctoring.api import (
    add_allowance_for_user,
    create_exam,
    create_exam_attempt,
    get_allowances_for_course,
)
from edx_proctoring.models import ProctoredExamStudentAttempt
from rest_framework import status
from rest_framework.test import APIClient

from common.djangoapps.student.tests.factories import InstructorFactory, UserFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

PROCTORING_SETTINGS = {
    'PROCTORING_BACKENDS': {
        'DEFAULT': 'null',
        'null': {},
    },
}


@override_settings(**PROCTORING_SETTINGS)
@patch.dict(settings.FEATURES, {'ENABLE_SPECIAL_EXAMS': True})
@ddt.ddt
class SpecialExamsListViewTest(ModuleStoreTestCase):
    """Tests for GET /api/instructor/v2/courses/{course_key}/special_exams"""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.course = CourseFactory.create(enable_proctored_exams=True)
        self.instructor = InstructorFactory.create(course_key=self.course.id)
        self.client.force_authenticate(user=self.instructor)
        self.course_id = str(self.course.id)
        self.timed_exam_id = create_exam(
            course_id=self.course_id,
            content_id='block-v1:test+test+test+type@sequential+block@timed',
            exam_name='Timed Exam',
            time_limit_mins=60,
            is_proctored=False,
        )
        self.proctored_exam_id = create_exam(
            course_id=self.course_id,
            content_id='block-v1:test+test+test+type@sequential+block@proctored',
            exam_name='Proctored Exam',
            time_limit_mins=90,
            is_proctored=True,
        )
        self.practice_exam_id = create_exam(
            course_id=self.course_id,
            content_id='block-v1:test+test+test+type@sequential+block@practice',
            exam_name='Practice Exam',
            time_limit_mins=30,
            is_proctored=True,
            is_practice_exam=True,
        )

    def _url(self):
        return reverse('instructor_api_v2:special_exams_list', kwargs={
            'course_id': self.course_id,
        })

    def test_list_exams(self):
        response = self.client.get(self._url())
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 3
        exams_by_name = {e['exam_name']: e for e in data}

        timed = exams_by_name['Timed Exam']
        assert timed['id'] == self.timed_exam_id
        assert timed['course_id'] == self.course_id
        assert timed['content_id'] == 'block-v1:test+test+test+type@sequential+block@timed'
        assert timed['time_limit_mins'] == 60
        assert timed['is_proctored'] is False
        assert timed['is_practice_exam'] is False
        assert timed['is_active'] is True
        assert timed['hide_after_due'] is False
        assert timed['exam_type'] == 'timed'

        proctored = exams_by_name['Proctored Exam']
        assert proctored['id'] == self.proctored_exam_id
        assert proctored['time_limit_mins'] == 90
        assert proctored['is_proctored'] is True
        assert proctored['is_practice_exam'] is False
        assert proctored['exam_type'] == 'proctored'

        practice = exams_by_name['Practice Exam']
        assert practice['id'] == self.practice_exam_id
        assert practice['time_limit_mins'] == 30
        assert practice['is_proctored'] is True
        assert practice['is_practice_exam'] is True
        assert practice['exam_type'] == 'practice'

    def test_unauthenticated(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(self._url())
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_unauthorized_student(self):
        student = UserFactory()
        self.client.force_authenticate(user=student)
        response = self.client.get(self._url())
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @ddt.data(
        ('timed', 'Timed Exam'),
        ('proctored', 'Proctored Exam'),
        ('practice', 'Practice Exam'),
    )
    @ddt.unpack
    def test_filter_by_exam_type(self, exam_type, expected_name):
        response = self.client.get(self._url(), {'exam_type': exam_type})
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]['exam_name'] == expected_name


@override_settings(**PROCTORING_SETTINGS)
@patch.dict(settings.FEATURES, {'ENABLE_SPECIAL_EXAMS': True})
class SpecialExamDetailViewTest(ModuleStoreTestCase):
    """Tests for GET /api/instructor/v2/courses/{course_key}/special_exams/{exam_id}"""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.course = CourseFactory.create(enable_proctored_exams=True)
        self.instructor = InstructorFactory.create(course_key=self.course.id)
        self.client.force_authenticate(user=self.instructor)
        self.course_id = str(self.course.id)
        self.exam_id = create_exam(
            course_id=self.course_id,
            content_id='block-v1:test+test+test+type@sequential+block@exam1',
            exam_name='Midterm Exam',
            time_limit_mins=60,
            is_proctored=False,
        )

    def _url(self, exam_id=None):
        return reverse('instructor_api_v2:special_exam_detail', kwargs={
            'course_id': self.course_id,
            'exam_id': exam_id or self.exam_id,
        })

    def test_get_exam(self):
        response = self.client.get(self._url())
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['exam_name'] == 'Midterm Exam'

    def test_exam_not_found(self):
        response = self.client.get(self._url(exam_id=99999))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_exam_wrong_course(self):
        other_course = CourseFactory.create()
        other_exam_id = create_exam(
            course_id=str(other_course.id),
            content_id='block-v1:other+other+other+type@sequential+block@exam1',
            exam_name='Other Exam',
            time_limit_mins=30,
            is_proctored=False,
        )
        response = self.client.get(self._url(exam_id=other_exam_id))
        assert response.status_code == status.HTTP_404_NOT_FOUND


@override_settings(**PROCTORING_SETTINGS)
@patch.dict(settings.FEATURES, {'ENABLE_SPECIAL_EXAMS': True})
class SpecialExamResetViewTest(ModuleStoreTestCase):
    """Tests for POST /api/instructor/v2/courses/{course_key}/special_exams/{exam_id}/reset/{username}"""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.course = CourseFactory.create(enable_proctored_exams=True)
        self.instructor = InstructorFactory.create(course_key=self.course.id)
        self.student = UserFactory(username='student1')
        self.client.force_authenticate(user=self.instructor)
        self.course_id = str(self.course.id)
        self.exam_id = create_exam(
            course_id=self.course_id,
            content_id='block-v1:test+test+test+type@sequential+block@exam1',
            exam_name='Midterm Exam',
            time_limit_mins=60,
            is_proctored=False,
        )

    def _url(self, exam_id=None, username='student1'):
        return reverse('instructor_api_v2:special_exam_reset', kwargs={
            'course_id': self.course_id,
            'exam_id': exam_id or self.exam_id,
            'username': username,
        })

    def test_reset_attempt(self):
        create_exam_attempt(self.exam_id, self.student.id)
        response = self.client.post(self._url())
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['success'] is True

    def test_reset_user_not_found(self):
        response = self.client.post(self._url(username='nonexistent'))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_reset_no_attempts(self):
        response = self.client.post(self._url())
        assert response.status_code == status.HTTP_404_NOT_FOUND


@override_settings(**PROCTORING_SETTINGS)
@patch.dict(settings.FEATURES, {'ENABLE_SPECIAL_EXAMS': True})
@ddt.ddt
class SpecialExamAttemptsViewTest(ModuleStoreTestCase):
    """Tests for GET /api/instructor/v2/courses/{course_key}/special_exams/{exam_id}/attempts"""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.course = CourseFactory.create(enable_proctored_exams=True)
        self.instructor = InstructorFactory.create(course_key=self.course.id)
        self.student = UserFactory(username='student1', email='student1@example.com')
        self.client.force_authenticate(user=self.instructor)
        self.course_id = str(self.course.id)

    def _create_exam(self, is_proctored=False, is_practice_exam=False, content_suffix='exam1'):
        return create_exam(
            course_id=self.course_id,
            content_id=f'block-v1:test+test+test+type@sequential+block@{content_suffix}',
            exam_name='Test Exam',
            time_limit_mins=60,
            is_proctored=is_proctored,
            is_practice_exam=is_practice_exam,
        )

    def _url(self, exam_id):
        return reverse('instructor_api_v2:special_exam_attempts', kwargs={
            'course_id': self.course_id,
            'exam_id': exam_id,
        })

    @ddt.data(
        (False, False, 'timed'),
        (True, False, 'proctored'),
        (True, True, 'practice'),
    )
    @ddt.unpack
    def test_attempt_exam_type(self, is_proctored, is_practice_exam, expected_type):
        exam_id = self._create_exam(is_proctored=is_proctored, is_practice_exam=is_practice_exam)
        create_exam_attempt(exam_id, self.student.id)
        response = self.client.get(self._url(exam_id))
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['count'] == 1
        assert data['results'][0]['exam_id'] == exam_id
        assert data['results'][0]['exam_name'] == 'Test Exam'
        assert data['results'][0]['exam_type'] == expected_type
        assert data['results'][0]['ready_to_resume'] is False
        assert data['results'][0]['user']['username'] == 'student1'

    def test_list_attempts_filters_by_exam(self):
        """Only attempts for the requested exam_id are returned."""
        exam_id = self._create_exam(content_suffix='exam1')
        other_exam_id = self._create_exam(content_suffix='exam2')
        create_exam_attempt(exam_id, self.student.id)
        other_student = UserFactory(username='student2')
        create_exam_attempt(other_exam_id, other_student.id)

        response = self.client.get(self._url(exam_id))
        data = response.json()
        assert data['count'] == 1
        assert data['results'][0]['exam_id'] == exam_id

    def test_ready_to_resume_true(self):
        """Verify ready_to_resume reflects the actual attempt state."""
        exam_id = self._create_exam()
        attempt_id = create_exam_attempt(exam_id, self.student.id)
        attempt = ProctoredExamStudentAttempt.objects.get(id=attempt_id)
        attempt.ready_to_resume = True
        attempt.save()

        response = self.client.get(self._url(exam_id))
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['results'][0]['ready_to_resume'] is True


@override_settings(**PROCTORING_SETTINGS)
@patch.dict(settings.FEATURES, {'ENABLE_SPECIAL_EXAMS': True})
class ProctoringSettingsViewTest(ModuleStoreTestCase):
    """Tests for GET/PATCH /api/instructor/v2/courses/{course_key}/proctoring_settings"""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.course = CourseFactory.create(
            enable_proctored_exams=True,
            proctoring_provider='null',
        )
        self.instructor = InstructorFactory.create(course_key=self.course.id)
        self.client.force_authenticate(user=self.instructor)
        self.course_id = str(self.course.id)

    def _url(self):
        return reverse('instructor_api_v2:proctoring_settings', kwargs={
            'course_id': self.course_id,
        })

    def test_get_settings(self):
        response = self.client.get(self._url())
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['enable_proctored_exams'] is True

    def test_patch_settings(self):
        response = self.client.patch(
            self._url(),
            data={'proctoring_escalation_email': 'proctor@example.com'},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['proctoring_escalation_email'] == 'proctor@example.com'

    def test_patch_invalid_data(self):
        response = self.client.patch(
            self._url(),
            data={'enable_proctored_exams': 'not_a_boolean'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@override_settings(**PROCTORING_SETTINGS)
@patch.dict(settings.FEATURES, {'ENABLE_SPECIAL_EXAMS': True})
class ExamAllowanceViewTest(ModuleStoreTestCase):
    """Tests for POST /api/instructor/v2/courses/{course_key}/special_exams/{exam_id}/allowance"""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.course = CourseFactory.create(enable_proctored_exams=True)
        self.instructor = InstructorFactory.create(course_key=self.course.id)
        self.student = UserFactory(username='student1')
        self.client.force_authenticate(user=self.instructor)
        self.course_id = str(self.course.id)
        self.exam_id = create_exam(
            course_id=self.course_id,
            content_id='block-v1:test+test+test+type@sequential+block@exam1',
            exam_name='Midterm Exam',
            time_limit_mins=60,
            is_proctored=False,
        )

    def _url(self, exam_id=None):
        return reverse('instructor_api_v2:exam_allowance', kwargs={
            'course_id': self.course_id,
            'exam_id': exam_id or self.exam_id,
        })

    def test_grant_allowance(self):
        response = self.client.post(
            self._url(),
            data={
                'user_ids': [self.student.username],
                'allowance_type': 'additional_time_granted',
                'value': '30',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['allowance_type'] == 'additional_time_granted'
        assert len(data['results']) == 1
        assert data['results'][0]['identifier'] == self.student.username
        assert data['results'][0]['success'] is True

    def test_grant_allowance_multiple_users(self):
        student2 = UserFactory(username='student2')
        response = self.client.post(
            self._url(),
            data={
                'user_ids': [self.student.username, student2.username],
                'allowance_type': 'additional_time_granted',
                'value': '30',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data['results']) == 2
        assert all(r['success'] is True for r in data['results'])

    def test_grant_allowance_missing_fields(self):
        response = self.client.post(
            self._url(),
            data={'user_ids': ['student1']},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_grant_allowance_exam_not_found(self):
        response = self.client.post(
            self._url(exam_id=99999),
            data={
                'user_ids': ['student1'],
                'allowance_type': 'additional_time_granted',
                'value': '30',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_allowance(self):
        """POST is an upsert — calling it twice updates the existing allowance."""
        self.client.post(
            self._url(),
            data={
                'user_ids': [self.student.username],
                'allowance_type': 'additional_time_granted',
                'value': '30',
            },
            format='json',
        )
        response = self.client.post(
            self._url(),
            data={
                'user_ids': [self.student.username],
                'allowance_type': 'additional_time_granted',
                'value': '60',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['results'][0]['success'] is True

    def test_grant_allowance_replaces_different_key(self):
        """Granting an allowance with a different key replaces the existing one (one per user+exam)."""
        self.client.post(
            self._url(),
            data={
                'user_ids': [self.student.username],
                'allowance_type': 'additional_time_granted',
                'value': '30',
            },
            format='json',
        )
        response = self.client.post(
            self._url(),
            data={
                'user_ids': [self.student.username],
                'allowance_type': 'review_policy_exception',
                'value': 'special review',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['results'][0]['success'] is True
        allowances = get_allowances_for_course(self.course_id)
        user_allowances = [a for a in allowances if a['user']['username'] == self.student.username]
        assert len(user_allowances) == 1
        assert user_allowances[0]['key'] == 'review_policy_exception'

    def test_delete_allowance(self):
        add_allowance_for_user(self.exam_id, self.student.username, 'additional_time_granted', '30')
        response = self.client.delete(
            self._url(),
            data={
                'user_ids': [self.student.username],
                'allowance_type': 'additional_time_granted',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['allowance_type'] == 'additional_time_granted'
        assert len(data['results']) == 1
        assert data['results'][0]['success'] is True

    def test_delete_allowance_multiple_users(self):
        student2 = UserFactory(username='student2')
        add_allowance_for_user(self.exam_id, self.student.username, 'additional_time_granted', '30')
        add_allowance_for_user(self.exam_id, student2.username, 'additional_time_granted', '30')
        response = self.client.delete(
            self._url(),
            data={
                'user_ids': [self.student.username, student2.username],
                'allowance_type': 'additional_time_granted',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()['results']) == 2
        assert all(r['success'] is True for r in response.json()['results'])

    def test_delete_allowance_missing_fields(self):
        response = self.client.delete(
            self._url(),
            data={'user_ids': [self.student.username]},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@override_settings(**PROCTORING_SETTINGS)
@patch.dict(settings.FEATURES, {'ENABLE_SPECIAL_EXAMS': True})
@ddt.ddt
class CourseAllowancesViewTest(ModuleStoreTestCase):
    """Tests for GET /api/instructor/v2/courses/{course_key}/special_exams/allowances"""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.course = CourseFactory.create(enable_proctored_exams=True)
        self.instructor = InstructorFactory.create(course_key=self.course.id)
        self.student = UserFactory(username='student1', email='student1@example.com')
        self.client.force_authenticate(user=self.instructor)
        self.course_id = str(self.course.id)
        self.exam_id = create_exam(
            course_id=self.course_id,
            content_id='block-v1:test+test+test+type@sequential+block@exam1',
            exam_name='Midterm Exam',
            time_limit_mins=60,
            is_proctored=False,
        )

    def _url(self):
        return reverse('instructor_api_v2:course_allowances', kwargs={
            'course_id': self.course_id,
        })

    def test_list_allowances(self):
        add_allowance_for_user(self.exam_id, self.student.username, 'additional_time_granted', '30')
        response = self.client.get(self._url())
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['count'] == 1
        assert data['results'][0]['key'] == 'additional_time_granted'
        assert data['results'][0]['user']['username'] == 'student1'

    def test_list_allowances_empty(self):
        response = self.client.get(self._url())
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['count'] == 0

    def test_search_allowances_by_username(self):
        add_allowance_for_user(self.exam_id, self.student.username, 'additional_time_granted', '30')
        response = self.client.get(self._url(), {'search': 'student1'})
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['count'] == 1

    def test_search_allowances_no_match(self):
        add_allowance_for_user(self.exam_id, self.student.username, 'additional_time_granted', '30')
        response = self.client.get(self._url(), {'search': 'nonexistent'})
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['count'] == 0

    def test_bulk_create_allowances(self):
        exam_id_2 = create_exam(
            course_id=self.course_id,
            content_id='block-v1:test+test+test+type@sequential+block@exam2',
            exam_name='Final Exam',
            time_limit_mins=120,
            is_proctored=False,
        )
        student2 = UserFactory(username='student2')
        response = self.client.post(
            self._url(),
            data={
                'exam_ids': [self.exam_id, exam_id_2],
                'user_ids': [self.student.username, student2.username],
                'allowance_type': 'additional_time_granted',
                'value': '30',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['allowance_type'] == 'additional_time_granted'
        assert data['value'] == '30'
        assert len(data['results']) == 4
        assert all(r['success'] is True for r in data['results'])

    def test_bulk_create_allowances_replaces_different_key(self):
        """Bulk-creating an allowance with a different key replaces the existing one."""
        add_allowance_for_user(self.exam_id, self.student.username, 'additional_time_granted', '30')
        response = self.client.post(
            self._url(),
            data={
                'exam_ids': [self.exam_id],
                'user_ids': [self.student.username],
                'allowance_type': 'review_policy_exception',
                'value': 'special review',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['results'][0]['success'] is True
        allowances = get_allowances_for_course(self.course_id)
        user_allowances = [a for a in allowances if a['user']['username'] == self.student.username]
        assert len(user_allowances) == 1
        assert user_allowances[0]['key'] == 'review_policy_exception'

    def test_bulk_create_allowances_missing_fields(self):
        response = self.client.post(
            self._url(),
            data={'exam_ids': [self.exam_id]},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @ddt.data(
        'username',
        'user.username',
        'email',
        'user.email',
        'exam_name',
        'proctored_exam.exam_name',
        'allowance_type',
        'key',
        'value',
    )
    def test_sort_allowances(self, ordering):
        """Verify all ordering fields are accepted and reverse correctly."""
        student2 = UserFactory(username='alice', email='alice@example.com')
        exam_id_2 = create_exam(
            course_id=self.course_id,
            content_id='block-v1:test+test+test+type@sequential+block@exam2',
            exam_name='AAA Exam',
            time_limit_mins=30,
            is_proctored=False,
        )
        add_allowance_for_user(self.exam_id, self.student.username, 'additional_time_granted', '30')
        add_allowance_for_user(exam_id_2, student2.username, 'review_policy_exception', '60')
        asc_response = self.client.get(self._url(), {'ordering': ordering})
        desc_response = self.client.get(self._url(), {'ordering': f'-{ordering}'})
        assert asc_response.status_code == status.HTTP_200_OK
        asc_results = asc_response.json()['results']
        desc_results = desc_response.json()['results']
        assert len(asc_results) == 2
        # All allowance fields differ between the two records, so order must reverse
        assert asc_results[0] == desc_results[1]
        assert asc_results[1] == desc_results[0]

    def test_sort_allowances_descending(self):
        student2 = UserFactory(username='alice', email='alice@example.com')
        add_allowance_for_user(self.exam_id, self.student.username, 'additional_time_granted', '30')
        add_allowance_for_user(self.exam_id, student2.username, 'additional_time_granted', '60')
        response = self.client.get(self._url(), {'ordering': '-username'})
        assert response.status_code == status.HTTP_200_OK
        results = response.json()['results']
        assert results[0]['user']['username'] == 'student1'
        assert results[1]['user']['username'] == 'alice'


@override_settings(**PROCTORING_SETTINGS)
@patch.dict(settings.FEATURES, {'ENABLE_SPECIAL_EXAMS': True})
@ddt.ddt
class CourseExamAttemptsViewTest(ModuleStoreTestCase):
    """Tests for GET /api/instructor/v2/courses/{course_key}/special_exams/attempts"""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.course = CourseFactory.create(enable_proctored_exams=True)
        self.instructor = InstructorFactory.create(course_key=self.course.id)
        self.student = UserFactory(username='student1', email='student1@example.com')
        self.client.force_authenticate(user=self.instructor)
        self.course_id = str(self.course.id)
        self.exam_id = create_exam(
            course_id=self.course_id,
            content_id='block-v1:test+test+test+type@sequential+block@exam1',
            exam_name='Midterm Exam',
            time_limit_mins=60,
            is_proctored=False,
        )

    def _url(self):
        return reverse('instructor_api_v2:course_exam_attempts', kwargs={
            'course_id': self.course_id,
        })

    def test_list_all_attempts(self):
        create_exam_attempt(self.exam_id, self.student.id)
        response = self.client.get(self._url())
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['count'] == 1
        assert data['results'][0]['exam_id'] == self.exam_id
        assert data['results'][0]['exam_name'] == 'Midterm Exam'
        assert data['results'][0]['ready_to_resume'] is False

    def test_search_attempts_by_username(self):
        create_exam_attempt(self.exam_id, self.student.id)
        response = self.client.get(self._url(), {'search': 'student1'})
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['count'] == 1

    def test_search_attempts_no_match(self):
        create_exam_attempt(self.exam_id, self.student.id)
        response = self.client.get(self._url(), {'search': 'nonexistent'})
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['count'] == 0

    @ddt.data(
        'username',
        'user.username',
        'email',
        'user.email',
        'exam_name',
        'proctored_exam.exam_name',
        'time_limit',
        'proctored_exam.time_limit_mins',
        'type',
        'started_at',
        'start_time',
        'completed_at',
        'end_time',
        'status',
    )
    def test_sort_attempts(self, ordering):
        """Verify all ordering fields produce reversed results for asc vs desc."""
        student2 = UserFactory(username='student2', email='student2@example.com')
        exam_id_2 = create_exam(
            course_id=self.course_id,
            content_id='block-v1:test+test+test+type@sequential+block@exam2',
            exam_name='Final Exam',
            time_limit_mins=120,
            is_proctored=True,
        )
        attempt_id_1 = create_exam_attempt(self.exam_id, self.student.id)
        create_exam_attempt(exam_id_2, student2.id)

        # Give attempt 1 a distinct completed_at and status so all fields differ
        attempt = ProctoredExamStudentAttempt.objects.get(id=attempt_id_1)
        attempt.started_at = timezone.now() - timezone.timedelta(hours=1)
        attempt.completed_at = timezone.now()
        attempt.status = 'submitted'
        attempt.save()

        asc_response = self.client.get(self._url(), {'ordering': ordering})
        desc_response = self.client.get(self._url(), {'ordering': f'-{ordering}'})
        assert asc_response.status_code == status.HTTP_200_OK
        assert desc_response.status_code == status.HTTP_200_OK
        asc_results = asc_response.json()['results']
        desc_results = desc_response.json()['results']
        assert len(asc_results) == 2
        assert asc_results[0] == desc_results[1]
        assert asc_results[1] == desc_results[0]

    def test_sort_attempts_descending(self):
        student2 = UserFactory(username='student2', email='student2@example.com')
        create_exam_attempt(self.exam_id, self.student.id)
        create_exam_attempt(self.exam_id, student2.id)
        response = self.client.get(self._url(), {'ordering': '-username'})
        assert response.status_code == status.HTTP_200_OK
        results = response.json()['results']
        assert results[0]['user']['username'] == 'student2'
        assert results[1]['user']['username'] == 'student1'

    def test_ready_to_resume_true(self):
        """Verify ready_to_resume reflects the actual attempt state."""
        attempt_id = create_exam_attempt(self.exam_id, self.student.id)
        attempt = ProctoredExamStudentAttempt.objects.get(id=attempt_id)
        attempt.ready_to_resume = True
        attempt.save()

        response = self.client.get(self._url())
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['results'][0]['ready_to_resume'] is True
