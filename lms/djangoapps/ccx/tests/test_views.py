"""
test views
"""


import datetime
import json
import re
from unittest.mock import MagicMock, patch

import ddt
import six
from ccx_keys.locator import CCXLocator
from django.conf import settings
from django.test import RequestFactory
from django.test.utils import override_settings
from django.urls import resolve, reverse
from django.utils.translation import gettext as _
from edx_django_utils.cache import RequestCache
from opaque_keys.edx.keys import CourseKey
from pytz import UTC
from xblocks_contrib.problem.capa.tests.response_xml_factory import StringResponseXMLFactory

from common.djangoapps.edxmako.shortcuts import render_to_response
from common.djangoapps.student.models import CourseEnrollment, CourseEnrollmentAllowed
from common.djangoapps.student.roles import CourseCcxCoachRole, CourseInstructorRole, CourseStaffRole
from common.djangoapps.student.tests.factories import AdminFactory, CourseEnrollmentFactory, UserFactory
from lms.djangoapps.ccx.models import CustomCourseForEdX
from lms.djangoapps.ccx.overrides import get_override_for_ccx, override_field_for_ccx
from lms.djangoapps.ccx.tests.factories import CcxFactory
from lms.djangoapps.ccx.tests.utils import CcxTestCase, flatten
from lms.djangoapps.ccx.utils import ccx_course, is_email
from lms.djangoapps.ccx.views import get_date
from lms.djangoapps.courseware.tabs import get_course_tab_list
from lms.djangoapps.courseware.tests.factories import StudentModuleFactory
from lms.djangoapps.courseware.tests.helpers import LoginEnrollmentTestCase
from lms.djangoapps.courseware.testutils import FieldOverrideTestMixin
from lms.djangoapps.discussion.django_comment_client.utils import has_forum_access
from lms.djangoapps.grades.api import task_compute_all_grades_for_course
from lms.djangoapps.instructor.access import allow_access, list_with_level
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.django_comment_common.models import FORUM_ROLE_ADMINISTRATOR
from openedx.core.djangoapps.django_comment_common.utils import are_permissions_roles_seeded
from openedx.core.lib.courses import get_course_by_id
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory, SampleCourseFactory
from xmodule.x_module import XModuleMixin


def intercept_renderer(path, context):
    """
    Intercept calls to `render_to_response` and attach the context dict to the
    response for examination in unit tests.
    """
    # I think Django already does this for you in their TestClient, except
    # we're bypassing that by using edxmako.  Probably edxmako should be
    # integrated better with Django's rendering and event system.
    response = render_to_response(path, context)
    response.mako_context = context
    response.mako_template = path
    return response


def ccx_dummy_request():
    """
    Returns dummy request object for CCX coach tab test
    """
    factory = RequestFactory()
    request = factory.get('ccx_coach_dashboard')
    request.user = MagicMock()

    return request


def setup_students_and_grades(context):
    """
    Create students and set their grades.
    :param context:  class reference
    """
    if context.course:
        context.student = student = UserFactory.create()
        CourseEnrollmentFactory.create(user=student, course_id=context.course.id)

        context.student2 = student2 = UserFactory.create(username='u\u0131\u028c\u0279\u0250\u026f')
        CourseEnrollmentFactory.create(user=student2, course_id=context.course.id)

        # create grades for self.student as if they'd submitted the ccx
        for chapter in context.course.get_children():
            for i, section in enumerate(chapter.get_children()):
                for j, problem in enumerate(section.get_children()):
                    # if not problem.visible_to_staff_only:
                    StudentModuleFactory.create(
                        grade=1 if i < j else 0,
                        max_grade=1,
                        student=context.student,
                        course_id=context.course.id,
                        module_state_key=problem.location
                    )

                    StudentModuleFactory.create(
                        grade=1 if i > j else 0,
                        max_grade=1,
                        student=context.student2,
                        course_id=context.course.id,
                        module_state_key=problem.location
                    )

        task_compute_all_grades_for_course.apply_async(kwargs={'course_key': str(context.course.id)})


def unhide(unit):
    """
    Recursively unhide a unit and all of its children in the CCX
    schedule.
    """
    unit['hidden'] = False
    for child in unit.get('children', ()):
        unhide(child)


class TestAdminAccessCoachDashboard(CcxTestCase, LoginEnrollmentTestCase):
    """
    Tests for Custom Courses views.
    """
    def setUp(self):
        super().setUp()
        self.make_coach()
        ccx = self.make_ccx()
        ccx_key = CCXLocator.from_course_locator(self.course.id, ccx.id)
        self.url = reverse('ccx_coach_dashboard', kwargs={'course_id': ccx_key})
        self.TEST_PASSWORD = 'Password1234'

    def test_staff_access_coach_dashboard(self):
        """
        User is staff, should access coach dashboard.
        """
        staff = self.make_staff()
        self.client.login(username=staff.username, password=self.TEST_PASSWORD)

        response = self.client.get(self.url)
        assert response.status_code == 200

    def test_instructor_access_coach_dashboard(self):
        """
        User is instructor, should access coach dashboard.
        """
        instructor = self.make_instructor()
        self.client.login(username=instructor.username, password=self.TEST_PASSWORD)

        # Now access URL
        response = self.client.get(self.url)
        assert response.status_code == 200

    def test_forbidden_user_access_coach_dashboard(self):
        """
        Assert user with no access must not see dashboard.
        """
        user = UserFactory.create(password=self.TEST_PASSWORD)
        self.client.login(username=user.username, password=self.TEST_PASSWORD)
        response = self.client.get(self.url)
        assert response.status_code == 403


@override_settings(
    XBLOCK_FIELD_DATA_WRAPPERS=['lms.djangoapps.courseware.field_overrides:OverrideModulestoreFieldData.wrap'],
    MODULESTORE_FIELD_OVERRIDE_PROVIDERS=['lms.djangoapps.ccx.overrides.CustomCoursesForEdxOverrideProvider'],
)
class TestCCXProgressChanges(CcxTestCase, LoginEnrollmentTestCase):
    """
    Tests ccx schedule changes in progress page
    """
    @classmethod
    def setUpClass(cls):
        """
        Set up tests
        """
        super().setUpClass()
        start = datetime.datetime(2016, 7, 1, 0, 0, tzinfo=UTC)
        due = datetime.datetime(2016, 7, 8, 0, 0, tzinfo=UTC)

        cls.course = course = CourseFactory.create(enable_ccx=True, start=start)
        chapter = BlockFactory.create(start=start, parent=course, category='chapter')
        sequential = BlockFactory.create(
            parent=chapter,
            start=start,
            due=due,
            category='sequential',
            metadata={'graded': True, 'format': 'Homework'}
        )
        vertical = BlockFactory.create(
            parent=sequential,
            start=start,
            due=due,
            category='vertical',
            metadata={'graded': True, 'format': 'Homework'}
        )

        # Trying to wrap the whole thing in a bulk operation fails because it
        # doesn't find the parents. But we can at least wrap this part...
        with cls.store.bulk_operations(course.id, emit_signals=False):
            flatten([BlockFactory.create(
                parent=vertical,
                start=start,
                due=due,
                category="problem",
                data=StringResponseXMLFactory().build_xml(answer='foo'),
                metadata={'rerandomize': 'always'}
            )] for _ in range(2))

    def assert_progress_summary(self, ccx_course_key, due):
        """
        assert signal and schedule update.
        """
        student = UserFactory.create(is_staff=False, password=self.TEST_PASSWORD)
        CourseEnrollment.enroll(student, ccx_course_key)
        assert CourseEnrollment.objects.filter(course_id=ccx_course_key, user=student).exists()

        # login as student
        self.client.login(username=student.username, password=self.TEST_PASSWORD)
        progress_page_response = self.client.get(
            reverse('progress', kwargs={'course_id': ccx_course_key})
        )
        grade_summary = progress_page_response.mako_context['courseware_summary']
        chapter = grade_summary[0]
        section = chapter['sections'][0]
        progress_page_due_date = section.due.strftime("%Y-%m-%d %H:%M")
        assert progress_page_due_date == due

    @patch('lms.djangoapps.ccx.views.render_to_response', intercept_renderer)
    @patch('lms.djangoapps.courseware.views.views.render_to_response', intercept_renderer)
    @patch.dict('django.conf.settings.FEATURES', {'CUSTOM_COURSES_EDX': True})
    def test_edit_schedule(self):
        """
        Get CCX schedule, modify it, save it.
        """
        self.make_coach()
        ccx = self.make_ccx()
        ccx_course_key = CCXLocator.from_course_locator(self.course.id, str(ccx.id))
        self.client.login(username=self.coach.username, password=self.TEST_PASSWORD)

        url = reverse('ccx_coach_dashboard', kwargs={'course_id': ccx_course_key})
        response = self.client.get(url)

        schedule = json.loads(response.mako_context['schedule'])
        assert len(schedule) == 1

        unhide(schedule[0])

        # edit schedule
        date = datetime.datetime.now() - datetime.timedelta(days=5)
        start = date.strftime("%Y-%m-%d %H:%M")
        due = (date + datetime.timedelta(days=3)).strftime("%Y-%m-%d %H:%M")

        schedule[0]['start'] = start
        schedule[0]['children'][0]['start'] = start
        schedule[0]['children'][0]['due'] = due
        schedule[0]['children'][0]['children'][0]['start'] = start
        schedule[0]['children'][0]['children'][0]['due'] = due

        url = reverse('save_ccx', kwargs={'course_id': ccx_course_key})
        response = self.client.post(url, json.dumps(schedule), content_type='application/json')

        assert response.status_code == 200

        schedule = json.loads(response.content.decode('utf-8'))['schedule']
        assert schedule[0]['hidden'] is False
        assert schedule[0]['start'] == start
        assert schedule[0]['children'][0]['start'] == start
        assert schedule[0]['children'][0]['due'] == due
        assert schedule[0]['children'][0]['children'][0]['due'] == due
        assert schedule[0]['children'][0]['children'][0]['start'] == start

        self.assert_progress_summary(ccx_course_key, due)


@override_settings(
    XBLOCK_FIELD_DATA_WRAPPERS=['lms.djangoapps.courseware.field_overrides:OverrideModulestoreFieldData.wrap'],
    MODULESTORE_FIELD_OVERRIDE_PROVIDERS=['lms.djangoapps.ccx.overrides.CustomCoursesForEdxOverrideProvider'],
)
@ddt.ddt
class TestCoachDashboard(CcxTestCase, LoginEnrollmentTestCase):
    """
    Tests for Custom Courses views.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course_disable_ccx = CourseFactory.create(enable_ccx=False)
        cls.course_with_ccx_connect_set = CourseFactory.create(enable_ccx=True, ccx_connector="http://ccx.com")

    def setUp(self):
        """
        Set up tests
        """
        super().setUp()
        # Login with the instructor account
        self.client.login(username=self.coach.username, password=self.TEST_PASSWORD)

        # adding staff to master course.
        staff = UserFactory()
        allow_access(self.course, staff, 'staff')
        assert CourseStaffRole(self.course.id).has_user(staff)

        # adding instructor to master course.
        instructor = UserFactory()
        allow_access(self.course, instructor, 'instructor')
        assert CourseInstructorRole(self.course.id).has_user(instructor)

    def test_not_a_coach(self):
        """
        User is not a coach, should get Forbidden response.
        """
        self.make_coach()
        ccx = self.make_ccx()

        # create session of non-coach user
        user = UserFactory.create(password=self.TEST_PASSWORD)
        self.client.login(username=user.username, password=self.TEST_PASSWORD)
        url = reverse(
            'ccx_coach_dashboard',
            kwargs={'course_id': CCXLocator.from_course_locator(self.course.id, ccx.id)})
        response = self.client.get(url)
        assert response.status_code == 403

    def test_no_ccx_created(self):
        """
        No CCX is created, coach should see form to add a CCX.
        """
        self.make_coach()
        url = reverse(
            'ccx_coach_dashboard',
            kwargs={'course_id': str(self.course.id)})
        response = self.client.get(url)
        assert response.status_code == 200
        assert re.search('<form action=".+create_ccx"', response.content.decode('utf-8'))

    def test_create_ccx_with_ccx_connector_set(self):
        """
        Assert that coach cannot create ccx when ``ccx_connector`` url is set.
        """
        role = CourseCcxCoachRole(self.course_with_ccx_connect_set.id)
        role.add_users(self.coach)

        url = reverse(
            'create_ccx',
            kwargs={'course_id': str(self.course_with_ccx_connect_set.id)})

        response = self.client.get(url)
        assert response.status_code == 200
        error_message = _(
            "A CCX can only be created on this course through an external service."
            " Contact a course admin to give you access."
        )
        assert re.search(error_message, response.content.decode('utf-8'))

    def test_create_ccx(self, ccx_name='New CCX'):  # noqa: PT028
        """
        Create CCX. Follow redirect to coach dashboard, confirm we see
        the coach dashboard for the new CCX.
        """

        self.make_coach()
        url = reverse(
            'create_ccx',
            kwargs={'course_id': str(self.course.id)})

        response = self.client.post(url, {'name': ccx_name})
        assert response.status_code == 302
        url = response.get('location')
        response = self.client.get(url)
        assert response.status_code == 200

        # Get the ccx_key
        path = six.moves.urllib.parse.urlparse(url).path
        resolver = resolve(path)
        ccx_key = resolver.kwargs['course_id']

        course_key = CourseKey.from_string(ccx_key)

        assert CourseEnrollment.is_enrolled(self.coach, course_key)
        assert re.search('id="ccx-schedule"', response.content.decode('utf-8'))

        # check if the max amount of student that can be enrolled has been overridden
        ccx = CustomCourseForEdX.objects.get()
        course_enrollments = get_override_for_ccx(ccx, self.course, 'max_student_enrollments_allowed')
        assert course_enrollments == settings.CCX_MAX_STUDENTS_ALLOWED
        # check if the course display name is properly set
        course_display_name = get_override_for_ccx(ccx, self.course, 'display_name')
        assert course_display_name == ccx_name

        # check if the course display name is properly set in modulestore
        course_display_name = self.mstore.get_course(ccx.locator).display_name
        assert course_display_name == ccx_name

        # assert ccx creator has role=staff
        role = CourseStaffRole(course_key)
        assert role.has_user(self.coach)

        # assert that staff and instructors of master course has staff and instructor roles on ccx
        list_staff_master_course = list_with_level(self.course.id, 'staff')
        list_instructor_master_course = list_with_level(self.course.id, 'instructor')

        # assert that forum roles are seeded
        assert are_permissions_roles_seeded(course_key)
        assert has_forum_access(self.coach.username, course_key, FORUM_ROLE_ADMINISTRATOR)

        with ccx_course(course_key) as course_ccx:
            list_staff_ccx_course = list_with_level(course_ccx.id, 'staff')
            # The "Coach" in the parent course becomes "Staff" on the CCX, so the CCX should have 1 "Staff"
            # user more than the parent course
            assert (len(list_staff_master_course) + 1) == len(list_staff_ccx_course)
            assert list_staff_master_course[0].email in [ccx_staff.email for ccx_staff in list_staff_ccx_course]
            # Make sure the "Coach" on the parent course is "Staff" on the CCX
            assert self.coach in list_staff_ccx_course

            list_instructor_ccx_course = list_with_level(course_ccx.id, 'instructor')
            assert len(list_instructor_ccx_course) == len(list_instructor_master_course)
            assert list_instructor_ccx_course[0].email == list_instructor_master_course[0].email

    @ddt.data("CCX demo 1", "CCX demo 2", "CCX demo 3")
    def test_create_multiple_ccx(self, ccx_name):
        self.test_create_ccx(ccx_name)

    def test_dashboard_access_of_disabled_ccx(self):
        """
        User should not see coach dashboard if ccx is disbale in studio.
        """
        ccx = CcxFactory(course_id=self.course_disable_ccx.id, coach=self.coach)
        url = reverse(
            'ccx_coach_dashboard',
            kwargs={'course_id': CCXLocator.from_course_locator(self.course_disable_ccx.id, ccx.id)})
        response = self.client.get(url)
        assert response.status_code == 404

    def test_dashboard_access_with_invalid_ccx_id(self):
        """
        User should not see coach dashboard if ccx id is invalid.
        """
        self.make_ccx()
        url = reverse(
            'ccx_coach_dashboard',
            kwargs={'course_id': CCXLocator.from_course_locator(self.course_disable_ccx.id, 700)})
        response = self.client.get(url)
        assert response.status_code == 404

    def test_get_date(self):
        """
        Assert that get_date returns valid date.
        """
        ccx = self.make_ccx()
        for section in self.course.get_children():
            assert get_date(ccx, section, 'start') == self.mooc_start
            assert get_date(ccx, section, 'due') is None
            for subsection in section.get_children():
                assert get_date(ccx, subsection, 'start') == self.mooc_start
                assert get_date(ccx, subsection, 'due') == self.mooc_due
                for unit in subsection.get_children():
                    assert get_date(ccx, unit, 'start', parent_node=subsection) == self.mooc_start
                    assert get_date(ccx, unit, 'due', parent_node=subsection) == self.mooc_due

    @patch('lms.djangoapps.ccx.views.render_to_response', intercept_renderer)
    @patch('lms.djangoapps.ccx.views.TODAY')
    def test_edit_schedule(self, today):
        """
        Get CCX schedule, modify it, save it.
        """
        today.return_value = datetime.datetime(2014, 11, 25, tzinfo=UTC)
        self.make_coach()
        ccx = self.make_ccx()
        url = reverse(
            'ccx_coach_dashboard',
            kwargs={'course_id': CCXLocator.from_course_locator(self.course.id, ccx.id)})
        response = self.client.get(url)
        schedule = json.loads(response.mako_context['schedule'])

        assert len(schedule) == 2
        assert schedule[0]['hidden'] is False
        # If a coach does not override dates, then dates will be imported from master course.
        assert schedule[0]['start'] == self.chapters[0].start.strftime('%Y-%m-%d %H:%M')
        assert schedule[0]['children'][0]['start'] == self.sequentials[0].start.strftime('%Y-%m-%d %H:%M')

        if self.sequentials[0].due:
            expected_due = self.sequentials[0].due.strftime('%Y-%m-%d %H:%M')
        else:
            expected_due = None
        assert schedule[0]['children'][0]['due'] == expected_due

        url = reverse(
            'save_ccx',
            kwargs={'course_id': CCXLocator.from_course_locator(self.course.id, ccx.id)})

        unhide(schedule[0])
        schedule[0]['start'] = '2014-11-20 00:00'
        schedule[0]['children'][0]['due'] = '2014-12-25 00:00'  # what a jerk!
        schedule[0]['children'][0]['children'][0]['start'] = '2014-12-20 00:00'
        schedule[0]['children'][0]['children'][0]['due'] = '2014-12-25 00:00'

        response = self.client.post(
            url, json.dumps(schedule), content_type='application/json'
        )

        schedule = json.loads(response.content.decode('utf-8'))['schedule']
        assert schedule[0]['hidden'] is False
        assert schedule[0]['start'] == '2014-11-20 00:00'
        assert schedule[0]['children'][0]['due'] == '2014-12-25 00:00'

        assert schedule[0]['children'][0]['children'][0]['due'] == '2014-12-25 00:00'
        assert schedule[0]['children'][0]['children'][0]['start'] == '2014-12-20 00:00'

        # Make sure start date set on course, follows start date of earliest
        # scheduled chapter
        ccx = CustomCourseForEdX.objects.get()
        course_start = get_override_for_ccx(ccx, self.course, 'start')
        assert str(course_start)[:(- 9)] == self.chapters[0].start.strftime('%Y-%m-%d %H:%M')

        # Make sure grading policy adjusted
        policy = get_override_for_ccx(ccx, self.course, 'grading_policy',
                                      self.course.grading_policy)
        assert policy['GRADER'][0]['type'] == 'Homework'
        assert policy['GRADER'][0]['min_count'] == 8
        assert policy['GRADER'][1]['type'] == 'Lab'
        assert policy['GRADER'][1]['min_count'] == 0
        assert policy['GRADER'][2]['type'] == 'Midterm Exam'
        assert policy['GRADER'][2]['min_count'] == 0
        assert policy['GRADER'][3]['type'] == 'Final Exam'
        assert policy['GRADER'][3]['min_count'] == 0

    @patch('lms.djangoapps.ccx.views.render_to_response', intercept_renderer)
    def test_save_without_min_count(self):
        """
        POST grading policy without min_count field.
        """
        self.make_coach()
        ccx = self.make_ccx()

        course_id = CCXLocator.from_course_locator(self.course.id, ccx.id)
        save_policy_url = reverse(
            'ccx_set_grading_policy', kwargs={'course_id': course_id})

        # This policy doesn't include a min_count field
        policy = {
            "GRADE_CUTOFFS": {
                "Pass": 0.5
            },
            "GRADER": [
                {
                    "weight": 0.15,
                    "type": "Homework",
                    "drop_count": 2,
                    "short_label": "HW"
                }
            ]
        }

        response = self.client.post(
            save_policy_url, {"policy": json.dumps(policy)}
        )
        assert response.status_code == 302

        ccx = CustomCourseForEdX.objects.get()

        # Make sure grading policy adjusted
        policy = get_override_for_ccx(
            ccx, self.course, 'grading_policy', self.course.grading_policy
        )
        assert len(policy['GRADER']) == 1
        assert policy['GRADER'][0]['type'] == 'Homework'
        assert 'min_count' not in policy['GRADER'][0]

        save_ccx_url = reverse('save_ccx', kwargs={'course_id': course_id})
        coach_dashboard_url = reverse(
            'ccx_coach_dashboard',
            kwargs={'course_id': course_id}
        )
        response = self.client.get(coach_dashboard_url)
        schedule = json.loads(response.mako_context['schedule'])
        response = self.client.post(
            save_ccx_url, json.dumps(schedule), content_type='application/json'
        )
        assert response.status_code == 200

    @ddt.data(
        ('ccx-manage-students', True, 1, 'student-ids', ('enrollment-button', 'Enroll')),
        ('ccx-manage-students', False, 0, 'student-ids', ('enrollment-button', 'Enroll')),
    )
    @ddt.unpack
    def test_enroll_member_student(self, view_name, send_email, outbox_count, student_form_input_name, button_tuple):
        """
        Tests the enrollment of  a list of students who are members
        of the class.

        It tests 2 different views that use slightly different parameters,
        but that perform the same task.
        """
        self.make_coach()
        ccx = self.make_ccx()
        enrollment = CourseEnrollmentFactory(course_id=self.course.id)
        student = enrollment.user
        outbox = self.get_outbox()
        assert not outbox

        url = reverse(
            view_name,
            kwargs={'course_id': CCXLocator.from_course_locator(self.course.id, ccx.id)}
        )
        data = {
            button_tuple[0]: button_tuple[1],
            student_form_input_name: ','.join([student.email, ]),
        }
        if send_email:
            data['email-students'] = 'Notify-students-by-email'
        response = self.client.post(url, data=data, follow=True)
        assert response.status_code == 200
        # we were redirected to our current location
        assert len(response.redirect_chain) == 1
        assert 302 in response.redirect_chain[0]
        assert len(outbox) == outbox_count
        if send_email:
            assert student.email in outbox[0].recipients()
        # a CcxMembership exists for this student
        assert CourseEnrollment.objects.filter(course_id=self.course.id, user=student).exists()

    def test_ccx_invite_enroll_up_to_limit(self):
        """
        Enrolls a list of students up to the enrollment limit.

        This test is specific to one of the enrollment views: the reason is because
        the view used in this test can perform bulk enrollments.
        """
        self.make_coach()
        # create ccx and limit the maximum amount of students that can be enrolled to 2
        ccx = self.make_ccx(max_students_allowed=2)
        ccx_course_key = CCXLocator.from_course_locator(self.course.id, ccx.id)
        staff = self.make_staff()
        instructor = self.make_instructor()

        # create some users
        students = [instructor, staff, self.coach] + [
            UserFactory.create(is_staff=False) for _ in range(3)
        ]

        url = reverse(
            'ccx-manage-students',
            kwargs={'course_id': ccx_course_key}
        )
        data = {
            'enrollment-button': 'Enroll',
            'student-ids': ','.join([student.email for student in students]),
        }
        response = self.client.post(url, data=data, follow=True)
        assert response.status_code == 200
        # even if course is coach can enroll staff and admins of master course into ccx
        assert CourseEnrollment.objects.filter(course_id=ccx_course_key, user=instructor).exists()
        assert CourseEnrollment.objects.filter(course_id=ccx_course_key, user=staff).exists()
        assert CourseEnrollment.objects.filter(course_id=ccx_course_key, user=self.coach).exists()

        # a CcxMembership exists for the first five students but not the sixth
        assert CourseEnrollment.objects.filter(course_id=ccx_course_key, user=students[3]).exists()
        assert CourseEnrollment.objects.filter(course_id=ccx_course_key, user=students[4]).exists()
        assert not CourseEnrollment.objects.filter(course_id=ccx_course_key, user=students[5]).exists()

    @ddt.data(
        ('ccx-manage-students', True, 1, 'student-ids', ('enrollment-button', 'Unenroll')),
        ('ccx-manage-students', False, 0, 'student-ids', ('enrollment-button', 'Unenroll')),
        ('ccx-manage-students', True, 1, 'student-id', ('student-action', 'revoke')),
        ('ccx-manage-students', False, 0, 'student-id', ('student-action', 'revoke')),
    )
    @ddt.unpack
    def test_unenroll_member_student(self, view_name, send_email, outbox_count, student_form_input_name, button_tuple):
        """
        Tests the unenrollment of a list of students who are members of the class.

        It tests 2 different views that use slightly different parameters,
        but that perform the same task.
        """
        self.make_coach()
        ccx = self.make_ccx()
        course_key = CCXLocator.from_course_locator(self.course.id, ccx.id)
        enrollment = CourseEnrollmentFactory(course_id=course_key)
        student = enrollment.user
        outbox = self.get_outbox()
        assert not outbox

        url = reverse(
            view_name,
            kwargs={'course_id': course_key}
        )
        data = {
            button_tuple[0]: button_tuple[1],
            student_form_input_name: ','.join([student.email, ]),
        }
        if send_email:
            data['email-students'] = 'Notify-students-by-email'
        response = self.client.post(url, data=data, follow=True)
        assert response.status_code == 200
        # we were redirected to our current location
        assert len(response.redirect_chain) == 1
        assert 302 in response.redirect_chain[0]
        assert len(outbox) == outbox_count
        if send_email:
            assert student.email in outbox[0].recipients()
        # a CcxMembership does not exists for this student
        assert not CourseEnrollment.objects.filter(course_id=self.course.id, user=student).exists()

    @ddt.data(
        ('ccx-manage-students', True, 1, 'student-ids', ('enrollment-button', 'Enroll'), 'nobody@nowhere.com'),
        ('ccx-manage-students', False, 0, 'student-ids', ('enrollment-button', 'Enroll'), 'nobody@nowhere.com'),
        ('ccx-manage-students', True, 0, 'student-ids', ('enrollment-button', 'Enroll'), 'nobody'),
        ('ccx-manage-students', False, 0, 'student-ids', ('enrollment-button', 'Enroll'), 'nobody'),
    )
    @ddt.unpack
    def test_enroll_non_user_student(
            self, view_name, send_email, outbox_count, student_form_input_name, button_tuple, identifier):
        """
        Tests the enrollment of a list of students who are not users yet.

        It tests 2 different views that use slightly different parameters,
        but that perform the same task.
        """
        self.make_coach()
        ccx = self.make_ccx()
        course_key = CCXLocator.from_course_locator(self.course.id, ccx.id)
        outbox = self.get_outbox()
        assert not outbox

        url = reverse(
            view_name,
            kwargs={'course_id': course_key}
        )
        data = {
            button_tuple[0]: button_tuple[1],
            student_form_input_name: ','.join([identifier, ]),
        }
        if send_email:
            data['email-students'] = 'Notify-students-by-email'
        response = self.client.post(url, data=data, follow=True)
        assert response.status_code == 200
        # we were redirected to our current location
        assert len(response.redirect_chain) == 1
        assert 302 in response.redirect_chain[0]
        assert len(outbox) == outbox_count

        # some error messages are returned for one of the views only
        if view_name == 'ccx_manage_student' and not is_email(identifier):
            self.assertContains(response, 'Could not find a user with name or email ', status_code=200)

        if is_email(identifier):
            if send_email:
                assert identifier in outbox[0].recipients()
            assert CourseEnrollmentAllowed.objects.filter(course_id=course_key, email=identifier).exists()
        else:
            assert not CourseEnrollmentAllowed.objects.filter(course_id=course_key, email=identifier).exists()

    @ddt.data(
        ('ccx-manage-students', True, 0, 'student-ids', ('enrollment-button', 'Unenroll'), 'nobody@nowhere.com'),
        ('ccx-manage-students', False, 0, 'student-ids', ('enrollment-button', 'Unenroll'), 'nobody@nowhere.com'),
        ('ccx-manage-students', True, 0, 'student-ids', ('enrollment-button', 'Unenroll'), 'nobody'),
        ('ccx-manage-students', False, 0, 'student-ids', ('enrollment-button', 'Unenroll'), 'nobody'),
    )
    @ddt.unpack
    def test_unenroll_non_user_student(
            self, view_name, send_email, outbox_count, student_form_input_name, button_tuple, identifier):
        """
        Unenroll a list of students who are not users yet
        """
        self.make_coach()
        course = CourseFactory.create()
        ccx = self.make_ccx()
        course_key = CCXLocator.from_course_locator(course.id, ccx.id)
        outbox = self.get_outbox()
        CourseEnrollmentAllowed(course_id=course_key, email=identifier)
        assert not outbox

        url = reverse(
            view_name,
            kwargs={'course_id': course_key}
        )
        data = {
            button_tuple[0]: button_tuple[1],
            student_form_input_name: ','.join([identifier, ]),
        }
        if send_email:
            data['email-students'] = 'Notify-students-by-email'
        response = self.client.post(url, data=data, follow=True)
        assert response.status_code == 200
        # we were redirected to our current location
        assert len(response.redirect_chain) == 1
        assert 302 in response.redirect_chain[0]
        assert len(outbox) == outbox_count
        assert not CourseEnrollmentAllowed.objects.filter(course_id=course_key, email=identifier).exists()


class TestCoachDashboardSchedule(CcxTestCase, LoginEnrollmentTestCase, ModuleStoreTestCase):
    """
    Tests of the CCX Coach Dashboard which need to modify the course content.
    """

    ENABLED_CACHES = ['default', 'mongo_inheritance_cache', 'loc_cache']

    def setUp(self):
        super().setUp()
        self.course = course = CourseFactory.create(enable_ccx=True)

        # Create a course outline
        self.mooc_start = start = datetime.datetime(
            2010, 5, 12, 2, 42, tzinfo=UTC
        )
        self.mooc_due = due = datetime.datetime(
            2010, 7, 7, 0, 0, tzinfo=UTC
        )

        self.chapters = [
            BlockFactory.create(start=start, parent=course) for _ in range(2)
        ]
        self.sequentials = flatten([
            [
                BlockFactory.create(parent=chapter) for _ in range(2)
            ] for chapter in self.chapters
        ])
        self.verticals = flatten([
            [
                BlockFactory.create(
                    start=start, due=due, parent=sequential, graded=True, format='Homework', category='vertical'
                ) for _ in range(2)
            ] for sequential in self.sequentials
        ])

        # Trying to wrap the whole thing in a bulk operation fails because it
        # doesn't find the parents. But we can at least wrap this part...
        with self.store.bulk_operations(course.id, emit_signals=False):
            blocks = flatten([  # pylint: disable=unused-variable  # noqa: F841
                [
                    BlockFactory.create(parent=vertical) for _ in range(2)
                ] for vertical in self.verticals
            ])

        # Create instructor account
        self.coach = UserFactory.create()
        # create an instance of modulestore
        self.mstore = modulestore()

        # Login with the instructor account
        self.client.login(username=self.coach.username, password=self.TEST_PASSWORD)

        # adding staff to master course.
        staff = UserFactory()
        allow_access(self.course, staff, 'staff')
        assert CourseStaffRole(self.course.id).has_user(staff)

        # adding instructor to master course.
        instructor = UserFactory()
        allow_access(self.course, instructor, 'instructor')
        assert CourseInstructorRole(self.course.id).has_user(instructor)

        assert modulestore().has_course(self.course.id)

    def assert_elements_in_schedule(self, url, n_chapters=2, n_sequentials=4, n_verticals=8):
        """
        Helper function to count visible elements in the schedule
        """
        response = self.client.get(url)
        assert response.status_code == 200
        # the schedule contains chapters
        chapters = json.loads(response.mako_context['schedule'])
        sequentials = flatten([chapter.get('children', []) for chapter in chapters])
        verticals = flatten([sequential.get('children', []) for sequential in sequentials])
        # check that the numbers of nodes at different level are the expected ones
        assert n_chapters == len(chapters)
        assert n_sequentials == len(sequentials)
        assert n_verticals == len(verticals)
        # extract the locations of all the nodes
        all_elements = chapters + sequentials + verticals
        return [elem['location'] for elem in all_elements if 'location' in elem]

    def hide_node(self, node):
        """
        Helper function to set the node `visible_to_staff_only` property
        to True and save the change
        """
        node.visible_to_staff_only = True
        self.mstore.update_item(node, self.coach.id)

    @patch('lms.djangoapps.ccx.views.render_to_response', intercept_renderer)
    @patch('lms.djangoapps.ccx.views.TODAY')
    def test_get_ccx_schedule(self, today):
        """
        Gets CCX schedule and checks number of blocks in it.
        Hides nodes at a different depth and checks that these nodes
        are not in the schedule.
        """
        today.return_value = datetime.datetime(2014, 11, 25, tzinfo=UTC)
        self.make_coach()
        ccx = self.make_ccx()
        url = reverse(
            'ccx_coach_dashboard',
            kwargs={
                'course_id': CCXLocator.from_course_locator(
                    self.course.id, ccx.id)
            }
        )
        # all the elements are visible
        self.assert_elements_in_schedule(url)
        # hide a vertical
        vertical = self.verticals[0]
        self.hide_node(vertical)
        locations = self.assert_elements_in_schedule(url, n_verticals=7)
        assert str(vertical.location) not in locations
        # hide a sequential
        sequential = self.sequentials[0]
        self.hide_node(sequential)
        locations = self.assert_elements_in_schedule(url, n_sequentials=3, n_verticals=6)
        assert str(sequential.location) not in locations
        # hide a chapter
        chapter = self.chapters[0]
        self.hide_node(chapter)
        locations = self.assert_elements_in_schedule(url, n_chapters=1, n_sequentials=2, n_verticals=4)
        assert str(chapter.location) not in locations


GET_CHILDREN = XModuleMixin.get_children


def patched_get_children(self, usage_key_filter=None):
    """Emulate system tools that mask courseware not visible to students"""
    def iter_children():
        """skip children not visible to students"""
        for child in GET_CHILDREN(self, usage_key_filter=usage_key_filter):
            child._field_data_cache = {}  # pylint: disable=protected-access
            if not child.visible_to_staff_only:
                yield child
    return list(iter_children())


@override_settings(
    XBLOCK_FIELD_DATA_WRAPPERS=['lms.djangoapps.courseware.field_overrides:OverrideModulestoreFieldData.wrap'],
    MODULESTORE_FIELD_OVERRIDE_PROVIDERS=['lms.djangoapps.ccx.overrides.CustomCoursesForEdxOverrideProvider'],
)
@patch('xmodule.x_module.XModuleMixin.get_children', patched_get_children, spec=True)
class TestCCXGrades(FieldOverrideTestMixin, SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Tests for Custom Courses views.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._course = course = CourseFactory.create(enable_ccx=True)
        CourseOverview.load_from_module_store(course.id)

        # Create a course outline
        cls.mooc_start = start = datetime.datetime(
            2010, 5, 12, 2, 42, tzinfo=UTC
        )
        chapter = BlockFactory.create(
            start=start, parent=course, category='sequential'
        )
        cls.sections = sections = [
            BlockFactory.create(
                parent=chapter,
                category="sequential",
                metadata={'graded': True, 'format': 'Homework'})
            for _ in range(4)
        ]
        # making problems available at class level for possible future use in tests
        cls.problems = [
            [
                BlockFactory.create(
                    parent=section,
                    category="problem",
                    data=StringResponseXMLFactory().build_xml(answer='foo'),
                    metadata={'rerandomize': 'always'}
                ) for _ in range(4)
            ] for section in sections
        ]

    def setUp(self):
        """
        Set up tests
        """
        super().setUp()

        # Create instructor account
        self.coach = coach = AdminFactory.create()
        self.client.login(username=coach.username, password=self.TEST_PASSWORD)

        # Create CCX
        role = CourseCcxCoachRole(self._course.id)
        role.add_users(coach)
        ccx = CcxFactory(course_id=self._course.id, coach=self.coach)

        # override course grading policy and make last section invisible to students
        override_field_for_ccx(ccx, self._course, 'grading_policy', {
            'GRADER': [
                {'drop_count': 0,
                 'min_count': 2,
                 'short_label': 'HW',
                 'type': 'Homework',
                 'weight': 1}
            ],
            'GRADE_CUTOFFS': {'Pass': 0.75},
        })
        override_field_for_ccx(
            ccx, self.sections[-1], 'visible_to_staff_only', True
        )

        # create a ccx locator and retrieve the course structure using that key
        # which emulates how a student would get access.
        self.ccx_key = CCXLocator.from_course_locator(self._course.id, str(ccx.id))
        self.course = get_course_by_id(self.ccx_key, depth=None)
        CourseOverview.load_from_module_store(self.course.id)
        setup_students_and_grades(self)
        self.client.login(username=coach.username, password=self.TEST_PASSWORD)
        self.addCleanup(RequestCache.clear_all_namespaces)
        from xmodule.modulestore.django import SignalHandler

        # using CCX object as sender here.
        SignalHandler.course_published.send(
            sender=ccx,
            course_key=self.ccx_key
        )

    @patch('lms.djangoapps.ccx.views.render_to_response', intercept_renderer)
    @patch('lms.djangoapps.instructor.views.gradebook_api.MAX_STUDENTS_PER_PAGE_GRADE_BOOK', 1)
    def test_gradebook(self):
        self.course.enable_ccx = True
        RequestCache.clear_all_namespaces()

        url = reverse(
            'ccx_gradebook',
            kwargs={'course_id': self.ccx_key}
        )
        response = self.client.get(url)
        assert response.status_code == 200
        # Max number of student per page is one.  Patched setting MAX_STUDENTS_PER_PAGE_GRADE_BOOK = 1
        assert len(response.mako_context['students']) == 1
        student_info = response.mako_context['students'][0]
        assert student_info['grade_summary']['percent'] == 0.5
        assert list(student_info['grade_summary']['grade_breakdown'].values())[0]['percent'] == 0.5
        assert len(student_info['grade_summary']['section_breakdown']) == 4

    def test_grades_csv(self):
        self.course.enable_ccx = True
        RequestCache.clear_all_namespaces()

        url = reverse(
            'ccx_grades_csv',
            kwargs={'course_id': self.ccx_key}
        )
        response = self.client.get(url)
        assert response.status_code == 200
        # Are the grades downloaded as an attachment?
        assert response['content-disposition'] == 'attachment'
        rows = response.content.decode('utf-8').strip().split('\r')
        headers = rows[0]
        # picking first student records
        data = dict(list(zip(headers.strip().split(','), rows[1].strip().split(','))))  # noqa: B905
        assert 'HW 04' not in data
        assert data['HW 01'] == '0.75'
        assert data['HW 02'] == '0.5'
        assert data['HW 03'] == '0.25'
        assert data['HW Avg'] == '0.5'

    @patch('lms.djangoapps.courseware.views.views.render_to_response', intercept_renderer)
    def test_student_progress(self):
        self.course.enable_ccx = True
        patch_context = patch('lms.djangoapps.courseware.views.views.get_course_with_access')
        get_course = patch_context.start()
        get_course.return_value = self.course
        self.addCleanup(patch_context.stop)

        self.client.login(username=self.student.username, password=self.TEST_PASSWORD)  # pylint: disable=no-member
        url = reverse(
            'progress',
            kwargs={'course_id': self.ccx_key}
        )
        response = self.client.get(url)
        assert response.status_code == 200
        grades = response.mako_context['grade_summary']
        assert grades['percent'] == 0.5
        assert list(grades['grade_breakdown'].values())[0]['percent'] == 0.5
        assert len(grades['section_breakdown']) == 4


@ddt.ddt
class CCXCoachTabTestCase(CcxTestCase):
    """
    Test case for CCX coach tab.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ccx_enabled_course = CourseFactory.create(enable_ccx=True)
        cls.ccx_disabled_course = CourseFactory.create(enable_ccx=False)

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        for course in [self.ccx_enabled_course, self.ccx_disabled_course]:
            CourseEnrollmentFactory.create(user=self.user, course_id=course.id)
            role = CourseCcxCoachRole(course.id)
            role.add_users(self.user)

    def check_ccx_tab(self, course, user):
        """Helper function for verifying the ccx tab."""
        all_tabs = get_course_tab_list(user, course)
        return any(tab.type == 'ccx_coach' for tab in all_tabs)

    @ddt.data(
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False),
        (True, None, False)
    )
    @ddt.unpack
    def test_coach_tab_for_ccx_advance_settings(self, ccx_feature_flag, enable_ccx, expected_result):
        """
        Test ccx coach tab state (visible or hidden) depending on the value of enable_ccx flag, ccx feature flag.
        """
        with self.settings(FEATURES={'CUSTOM_COURSES_EDX': ccx_feature_flag}):
            course = self.ccx_enabled_course if enable_ccx else self.ccx_disabled_course
            assert expected_result == self.check_ccx_tab(course, self.user)

    def test_ccx_tab_visibility_for_staff_when_not_coach_master_course(self):
        """
        Staff cannot view ccx coach dashboard on master course by default.
        """
        staff = self.make_staff()
        assert not self.check_ccx_tab(self.course, staff)

    def test_ccx_tab_visibility_for_staff_when_coach_master_course(self):
        """
        Staff can view ccx coach dashboard only if he is coach on master course.
        """
        staff = self.make_staff()
        role = CourseCcxCoachRole(self.course.id)
        role.add_users(staff)
        assert self.check_ccx_tab(self.course, staff)

    def test_ccx_tab_visibility_for_staff_ccx_course(self):
        """
        Staff can access coach dashboard on ccx course.
        """
        self.make_coach()
        ccx = self.make_ccx()
        ccx_key = CCXLocator.from_course_locator(self.course.id, str(ccx.id))
        staff = self.make_staff()

        with ccx_course(ccx_key) as course_ccx:
            allow_access(course_ccx, staff, 'staff')
            assert self.check_ccx_tab(course_ccx, staff)

    def test_ccx_tab_visibility_for_instructor_when_not_coach_master_course(self):
        """
        Instructor cannot view ccx coach dashboard on master course by default.
        """
        instructor = self.make_instructor()
        assert not self.check_ccx_tab(self.course, instructor)

    def test_ccx_tab_visibility_for_instructor_when_coach_master_course(self):
        """
        Instructor can view ccx coach dashboard only if he is coach on master course.
        """
        instructor = self.make_instructor()
        role = CourseCcxCoachRole(self.course.id)
        role.add_users(instructor)
        assert self.check_ccx_tab(self.course, instructor)

    def test_ccx_tab_visibility_for_instructor_ccx_course(self):
        """
        Instructor can access coach dashboard on ccx course.
        """
        self.make_coach()
        ccx = self.make_ccx()
        ccx_key = CCXLocator.from_course_locator(self.course.id, str(ccx.id))
        instructor = self.make_instructor()

        with ccx_course(ccx_key) as course_ccx:
            allow_access(course_ccx, instructor, 'instructor')
            assert self.check_ccx_tab(course_ccx, instructor)


class TestStudentViewsWithCCX(ModuleStoreTestCase):
    """
    Test to ensure that the student dashboard and courseware works for users enrolled in CCX
    courses.
    """

    def setUp(self):
        """
        Set up courses and enrollments.
        """
        super().setUp()

        # Create a Split Mongo course and enroll a student user in it.
        self.student_password = "foobar"
        self.student = UserFactory.create(username=self.TEST_PASSWORD, password=self.student_password, is_staff=False)
        self.split_course = SampleCourseFactory.create(default_store=ModuleStoreEnum.Type.split)
        CourseEnrollment.enroll(self.student, self.split_course.id)

        # Create a CCX coach.
        self.coach = AdminFactory.create()
        role = CourseCcxCoachRole(self.split_course.id)
        role.add_users(self.coach)

        # Create a CCX course and enroll the user in it.
        self.ccx = CcxFactory(course_id=self.split_course.id, coach=self.coach)
        last_week = datetime.datetime.now(UTC) - datetime.timedelta(days=7)
        override_field_for_ccx(self.ccx, self.split_course, 'start', last_week)  # Required by self.ccx.has_started().
        self.ccx_course_key = CCXLocator.from_course_locator(self.split_course.id, self.ccx.id)
        CourseEnrollment.enroll(self.student, self.ccx_course_key)

    def test_load_student_dashboard(self):
        self.client.login(username=self.student.username, password=self.student_password)
        response = self.client.get(reverse('dashboard'))
        assert response.status_code == 200
        assert re.search('Test CCX', response.content.decode('utf-8'))

    def test_load_courseware(self):
        self.client.login(username=self.student.username, password=self.student_password)
        sequence_key = self.ccx_course_key.make_usage_key('sequential', 'sequential_x1')
        response = self.client.get(reverse('render_xblock', args=[str(sequence_key)]))
        assert response.status_code == 200
