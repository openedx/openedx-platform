"""
Tests for Progress Tab API in the Course Home API
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import dateutil
import ddt
from django.urls import reverse
from django.utils.timezone import now
from edx_toggles.toggles.testutils import override_waffle_flag
from pytz import UTC

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.course_home_api.models import DisableProgressPageStackedConfig
from lms.djangoapps.course_home_api.tests.utils import BaseCourseHomeTests
from lms.djangoapps.course_home_api.toggles import COURSE_HOME_MICROFRONTEND_PROGRESS_TAB
from lms.djangoapps.grades.api import CourseGradeFactory
from lms.djangoapps.grades.constants import GradeOverrideFeatureEnum
from lms.djangoapps.grades.models import (
    PersistentCourseGrade,
    PersistentSubsectionGrade,
    PersistentSubsectionGradeOverride
)
from lms.djangoapps.grades.tests.utils import answer_problem
from lms.djangoapps.verify_student.models import ManualVerification
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.course_date_signals.utils import MIN_DURATION
from openedx.core.djangolib.testing.utils import get_mock_request
from openedx.features.content_type_gating.helpers import CONTENT_GATING_PARTITION_ID, CONTENT_TYPE_GATE_GROUP_IDS
from openedx.features.content_type_gating.models import ContentTypeGatingConfig
from openedx.features.course_duration_limits.models import CourseDurationLimitConfig
from xmodule.modulestore.tests.factories import BlockFactory


@ddt.ddt
@override_waffle_flag(COURSE_HOME_MICROFRONTEND_PROGRESS_TAB, active=True)
class ProgressTabTestViews(BaseCourseHomeTests):
    """
    Tests for the Progress Tab API
    """
    def setUp(self):
        super().setUp()
        self.url = reverse('course-home:progress-tab', args=[self.course.id])

    def add_subsection_with_problem(self, **kwargs):
        """Makes a chapter -> problem chain, and sets up the subsection as requested, returning the problem"""
        chapter = BlockFactory(parent=self.course, category='chapter')
        subsection = BlockFactory(parent=chapter, category='sequential', graded=True, **kwargs)
        vertical = BlockFactory(parent=subsection, category='vertical', graded=True)
        problem = BlockFactory(parent=vertical, category='problem', graded=True)
        return problem

    @ddt.data(CourseMode.AUDIT, CourseMode.VERIFIED)
    def test_get_authenticated_enrolled_user(self, enrollment_mode):
        CourseEnrollment.enroll(self.user, self.course.id, enrollment_mode)
        response = self.client.get(self.url)
        assert response.status_code == 200

        assert response.data['section_scores'] is not None
        for chapter in response.data['section_scores']:
            assert chapter is not None
        assert 'settings/grading/' + str(self.course.id) in response.data['studio_url']
        assert response.data['verification_data'] is not None
        assert response.data['verification_data']['status'] == 'none'
        if enrollment_mode == CourseMode.VERIFIED:
            ManualVerification.objects.create(user=self.user, status='approved')
            response = self.client.get(self.url)
            assert response.data['verification_data']['status'] == 'approved'
            assert response.data['certificate_data'] is None
        elif enrollment_mode == CourseMode.AUDIT:
            assert response.data['certificate_data']['cert_status'] == 'audit_passing'

    @ddt.data(True, False)
    def test_get_authenticated_user_not_enrolled(self, has_previously_enrolled):
        if has_previously_enrolled:
            # Create an enrollment, then unenroll to set is_active to False
            CourseEnrollment.enroll(self.user, self.course.id)
            CourseEnrollment.unenroll(self.user, self.course.id)
        response = self.client.get(self.url)
        assert response.status_code == 401

    def test_get_unauthenticated_user(self):
        self.client.logout()
        response = self.client.get(self.url)
        assert response.status_code == 401

    def test_get_unknown_course(self):
        url = reverse('course-home:progress-tab', args=['course-v1:unknown+course+2T2020'])
        response = self.client.get(url)
        assert response.status_code == 404

    @override_waffle_flag(COURSE_HOME_MICROFRONTEND_PROGRESS_TAB, active=False)
    @ddt.data(CourseMode.AUDIT, CourseMode.VERIFIED)
    def test_waffle_flag_disabled(self, enrollment_mode):
        CourseEnrollment.enroll(self.user, self.course.id, enrollment_mode)
        response = self.client.get(self.url)
        assert response.status_code == 404

    def test_masquerade(self):
        # Enroll a verified user
        verified_user = UserFactory(is_staff=False)
        CourseEnrollment.enroll(verified_user, self.course.id, CourseMode.VERIFIED)

        # Enroll self in course
        CourseEnrollment.enroll(self.user, self.course.id)
        response = self.client.get(self.url)
        assert response.status_code == 200

        self.switch_to_staff()  # needed for masquerade
        assert self.client.get(self.url).data.get('enrollment_mode') is None

        # Masquerade as verified user
        self.update_masquerade(username=verified_user.username)
        assert self.client.get(self.url).data.get('enrollment_mode') == 'verified'

    def test_masquerade_uses_masqueraded_permissions_for_show_grades(self):
        """
        Test that when a staff user is masquerading as a verified learner,
        the grade visibility for subsections with show_correctness='past_due' is
        determined based on the verified learner's permissions, not the staff user's
        permissions.
        """
        subsection_name = 'Masquerade grade visibility subsection'
        chapter = BlockFactory(parent=self.course, category='chapter')
        subsection = BlockFactory(
            parent=chapter,
            category='sequential',
            display_name=subsection_name,
            graded=True,
            due=now() + timedelta(days=30),
            show_correctness='past_due',
        )
        vertical = BlockFactory(parent=subsection, category='vertical', graded=True)
        BlockFactory(parent=vertical, category='problem', graded=True)

        verified_user = UserFactory(is_staff=False)
        CourseEnrollment.enroll(verified_user, self.course.id, CourseMode.VERIFIED)

        self.switch_to_staff()  # needed for masquerade

        def get_subsection_show_grades(response):
            for chapter in response.data['section_scores']:
                for subsection in chapter['subsections']:
                    if subsection['display_name'] == subsection_name:
                        return subsection['show_grades']
            assert False, f'Subsection {subsection_name} not found in section_scores'

        # Staff can see grades even when show_correctness is `past_due` and the due date has not passed.
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert get_subsection_show_grades(response) is True

        # When masquerading, grade visibility should follow the masqueraded learner permissions.
        self.update_masquerade(username=verified_user.username)
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert get_subsection_show_grades(response) is False

    @patch.dict('django.conf.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test_has_scheduled_content_data(self):
        CourseEnrollment.enroll(self.user, self.course.id)
        future = now() + timedelta(days=30)
        BlockFactory(parent=self.course, category='chapter', start=future)
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert response.json()['has_scheduled_content']

    def test_end(self):
        CourseEnrollment.enroll(self.user, self.course.id)
        future = now() + timedelta(days=30)
        self.course.end = future
        self.update_course(self.course, self.user.id)
        response = self.client.get(self.url)
        assert response.status_code == 200
        end = dateutil.parser.parse(response.json()['end']).replace(tzinfo=UTC)
        assert end.date() == future.date()

    def test_user_has_passing_grade(self):
        CourseEnrollment.enroll(self.user, self.course.id)
        self.course.grade_cutoffs = {'Pass': 0}
        self.update_course(self.course, self.user.id)
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert response.json()['user_has_passing_grade']

    def test_verified_mode(self):
        enrollment = CourseEnrollment.enroll(self.user, self.course.id)
        CourseDurationLimitConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))

        response = self.client.get(self.url)
        assert response.status_code == 200
        assert response.data['verified_mode'] == {'access_expiration_date': (enrollment.created + MIN_DURATION),
                                                  'currency': 'USD', 'currency_symbol': '$', 'price': 149,
                                                  'sku': 'ABCD1234', 'upgrade_url': '/dashboard'}

    def test_page_respects_stacked_config(self):
        CourseEnrollment.enroll(self.user, self.course.id)
        course_overview = CourseOverview.get_from_id(self.course.id)

        response = self.client.get(self.url)
        assert response.status_code == 200

        DisableProgressPageStackedConfig.objects.create(disabled=True, course=course_overview)

        response = self.client.get(self.url)
        assert response.status_code == 404

    def test_learner_has_access(self):
        chapter = BlockFactory(parent=self.course, category='chapter')
        gated = BlockFactory(parent=chapter, category='sequential')
        BlockFactory.create(parent=gated, category='problem', graded=True, has_score=True)
        ungated = BlockFactory(parent=chapter, category='sequential')
        BlockFactory.create(parent=ungated, category='problem', graded=True, has_score=True,
                            group_access={
                                CONTENT_GATING_PARTITION_ID: [CONTENT_TYPE_GATE_GROUP_IDS['full_access'],
                                                              CONTENT_TYPE_GATE_GROUP_IDS['limited_access']],
                            })

        CourseEnrollment.enroll(self.user, self.course.id)
        CourseDurationLimitConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))
        ContentTypeGatingConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))

        response = self.client.get(self.url)
        assert response.status_code == 200

        sections = response.data['section_scores']
        ungraded_score = sections[0]['subsections'][0]  # default sequence that parent class gives us
        gated_score = sections[1]['subsections'][0]
        ungated_score = sections[1]['subsections'][1]
        assert ungraded_score['learner_has_access']
        assert not gated_score['learner_has_access']
        assert ungated_score['learner_has_access']

    def test_override_is_visible(self):
        chapter = BlockFactory(parent=self.course, category='chapter')
        subsection = BlockFactory.create(parent=chapter, category="sequential", display_name="Subsection")

        CourseEnrollment.enroll(self.user, self.course.id)
        course_grade_params = {
            "user_id": self.user.id,
            "course_id": self.course.id,
            "percent_grade": 77.7,
            "letter_grade": "pass",
            "passed": True,
        }
        PersistentCourseGrade.update_or_create(**course_grade_params)

        params = {
            "user_id": self.user.id,
            "usage_key": subsection.location,
            "course_version": self.course.course_version,
            "subtree_edited_timestamp": "2016-08-01 18:53:24.354741Z",
            "earned_all": 6.0,
            "possible_all": 12.0,
            "earned_graded": 6.0,
            "possible_graded": 8.0,
            "visible_blocks": [],
            "first_attempted": datetime.now(),
        }

        created_grade = PersistentSubsectionGrade.update_or_create_grade(**params)
        proctoring_failure_comment = "Failed Test Proctoring"
        PersistentSubsectionGradeOverride.update_or_create_override(
            requesting_user=self.staff_user,
            subsection_grade_model=created_grade,
            earned_all_override=0.0,
            earned_graded_override=0.0,
            system=GradeOverrideFeatureEnum.proctoring,
            feature=GradeOverrideFeatureEnum.proctoring,
            comment=proctoring_failure_comment
        )

        response = self.client.get(self.url)
        assert response.status_code == 200

        sections = response.data['section_scores']
        overridden_subsection = sections[1]['subsections'][0]
        override_entry = overridden_subsection["override"]

        assert override_entry['system'] == GradeOverrideFeatureEnum.proctoring
        assert override_entry['reason'] == proctoring_failure_comment

    def test_view_other_students_progress_page(self):
        # Test the ability to view progress pages of other students by changing the url
        CourseEnrollment.enroll(self.user, self.course.id)
        response = self.client.get(self.url)
        assert response.data['username'] == self.user.username

        other_user = UserFactory()
        self.url = reverse('course-home:progress-tab-other-student', args=[self.course.id, other_user.id])
        CourseEnrollment.enroll(other_user, self.course.id)

        # users with the ccx coach role can view other students' progress pages
        with patch(
            'lms.djangoapps.course_home_api.progress.views.has_ccx_coach_role',
            return_value=True
        ):
            response = self.client.get(self.url)
            assert response.data['username'] == other_user.username

        # staff users can view other students' progress pages
        self.switch_to_staff()

        response = self.client.get(self.url)
        assert response.data['username'] == other_user.username

    def test_url_hidden_if_subsection_hide_after_due(self):
        chapter = BlockFactory(parent=self.course, category='chapter')
        yesterday = now() - timedelta(days=1)
        BlockFactory(parent=chapter, category='sequential', hide_after_due=True, due=yesterday)

        CourseEnrollment.enroll(self.user, self.course.id)

        response = self.client.get(self.url)
        assert response.status_code == 200

        sections = response.data['section_scores']
        regular_subsection = sections[0]['subsections'][0]  # default sequence that parent class gives us
        hide_after_due_subsection = sections[1]['subsections'][0]
        assert regular_subsection['url'] is not None
        assert hide_after_due_subsection['url'] is None

    @ddt.data(
        (True, 0.72),  # lab, midterm and final are visible to staff
        (False, 0.32),  # Only lab and midterm is visible to learners
    )
    @ddt.unpack
    def test_course_grade_considers_subsection_grade_visibility(self, is_staff, expected_percent):
        """
        Verify that the grade & is_passing info we send out is for visible grades only.

        Assumes that grading policy is the default one (search for DEFAULT_GRADING_POLICY).
        """
        if is_staff:
            self.switch_to_staff()
        CourseEnrollment.enroll(self.user, self.course.id)

        tomorrow = now() + timedelta(days=1)
        with self.store.bulk_operations(self.course.id):
            never = self.add_subsection_with_problem(format='Homework', show_correctness='never')
            always = self.add_subsection_with_problem(format='Midterm Exam', show_correctness='always')
            past_due = self.add_subsection_with_problem(format='Final Exam', show_correctness='past_due', due=tomorrow)
            never_but_show_grade = self.add_subsection_with_problem(
                format='Lab', show_correctness='never_but_include_grade'
            )

        answer_problem(self.course, get_mock_request(self.user), never)
        answer_problem(self.course, get_mock_request(self.user), always)
        answer_problem(self.course, get_mock_request(self.user), past_due)
        answer_problem(self.course, get_mock_request(self.user), never_but_show_grade)

        # First, confirm the grade in the database - it should never change based on user state.
        # This is midterm and final and a single problem added together.
        assert CourseGradeFactory().read(self.user, self.course).percent == 0.73

        response = self.client.get(self.url)
        assert response.status_code == 200
        assert response.data['course_grade']['percent'] == expected_percent
        assert response.data['course_grade']['is_passing'] == (expected_percent >= 0.5)
