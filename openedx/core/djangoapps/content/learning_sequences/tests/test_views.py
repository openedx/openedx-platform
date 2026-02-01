"""
Tests for the learning_sequences REST API.

The goals of this module are to:
* Test the serialization of the REST API.
* Make sure that arguments to the REST API (like masquerading) work properly.
* Prevent accidental breaking of backwards compatibility as we add to the API.

Testing the fine grained logic of, "What should be visible/accessible when you
have a Course + User with these properties?" is the responsibility of the
.api.tests package of this app.

Where possible, seed data using public API methods (e.g. replace_course_outline
from this app, edx-when's set_dates_for_course).
"""
from datetime import datetime, timezone

import ddt
from opaque_keys.edx.keys import CourseKey
from rest_framework.test import APITestCase, APIClient

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.courseware.tests.helpers import MasqueradeMixin
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase, skip_unless_lms
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from ..api import replace_course_outline
from ..api.tests.test_data import generate_sections
from ..data import CourseOutlineData, CourseVisibility


@skip_unless_lms
class CourseOutlineViewTest(CacheIsolationTestCase, APITestCase, ModuleStoreTestCase):
    """
    General tests for the CourseOutline.
    """

    @classmethod
    def setUpTestData(cls):  # lint-amnesty, pylint: disable=super-method-not-called
        cls.staff = UserFactory.create(
            username='staff', email='staff@example.com', is_staff=True, password='staff_pass'
        )
        cls.student = UserFactory.create(
            username='student', email='student@example.com', is_staff=False, password='student_pass'
        )
        cls.course_key = CourseKey.from_string("course-v1:OpenEdX+Seq+View")
        cls.course_url = outline_url(cls.course_key)
        cls.outline = CourseOutlineData(
            course_key=cls.course_key,
            title="Views Test Course!",
            published_at=datetime(2020, 5, 20, tzinfo=timezone.utc),
            published_version="5ebece4b69dd593d82fe2020",
            entrance_exam_id=None,
            days_early_for_beta=None,
            sections=generate_sections(cls.course_key, [2, 2]),
            self_paced=False,
            course_visibility=CourseVisibility.PUBLIC
        )
        replace_course_outline(cls.outline)

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org=self.course_key.org, number=self.course_key.course, run=self.course_key.run, display_name="Test Course"
        )
        self.client = APIClient()

    def test_non_existent_course_404(self):
        """
        We should 404, not 500, when asking for a course that isn't there.
        """
        self.client.login(username='staff', password='staff_pass')
        fake_course_key = self.course_key.replace(run="not_real")
        result = self.client.get(outline_url(fake_course_key))
        assert result.status_code == 404

    def test_non_existent_course_401_as_anonymous(self):
        """
        We should 401, not 404, when asking for a course that isn't there for an anonymous user.
        """
        fake_course_key = self.course_key.replace(run="not_real")
        result = self.client.get(outline_url(fake_course_key))
        assert result.status_code == 401

    def test_deprecated_course_key(self):
        """
        For now, make sure you need staff access bits to use the API.

        This is a temporary safeguard until the API is more complete.
        """
        self.client.login(username='staff', password='staff_pass')
        old_course_key = CourseKey.from_string("OldOrg/OldCourse/OldRun")
        result = self.client.get(outline_url(old_course_key))
        assert result.status_code == 400

    def test_outline_as_staff(self):
        """
        This is a pretty rudimentary test of a course that's returned.

        We'll want to flesh this out in a lot more detail once the API is more
        complete and the format more stable.
        """
        self.client.login(username='staff', password='staff_pass')
        result = self.client.get(self.course_url)
        data = result.data
        assert data['course_key'] == str(self.course_key)
        assert data['user_id'] == self.staff.id
        assert data['username'] == 'staff'

        # API test client automatically parses these into dates. Should we do
        # the raw form for max compatibility (i.e. guard against serialization
        # of dates changing), or do the parsing for conveninece? Convenience for
        # now.
        assert data['published_at'] == datetime(2020, 5, 20, tzinfo=timezone.utc)
        assert data['published_version'] == "5ebece4b69dd593d82fe2020"

        # Basic outline structure checks
        assert len(data['outline']['sections']) == 2
        assert len(data['outline']['sections'][0]['sequence_ids']) == 2
        assert len(data['outline']['sections'][1]['sequence_ids']) == 2
        assert len(data['outline']['sequences']) == 4


@ddt.ddt
@skip_unless_lms
class CourseOutlineViewTargetUserTest(CacheIsolationTestCase, APITestCase, ModuleStoreTestCase):
    """
    Tests permissions of specifying a target user via url parameter.
    """
    @classmethod
    def setUpTestData(cls):  # lint-amnesty, pylint: disable=super-method-not-called
        """Set up the basic course outline and our users."""
        # This is the course that we're creating staff for.
        cls.course_key = CourseKey.from_string("course-v1:OpenEdX+Masq+StaffAccess")

        # This is the course that our users are not course staff for
        cls.not_staff_course_key = CourseKey.from_string("course-v1:OpenEdX+Masq+NoStaffAccess")

        for course_key in [cls.course_key, cls.not_staff_course_key]:
            outline = CourseOutlineData(
                course_key=course_key,
                title="Views Test Course!",
                published_at=datetime(2020, 5, 20, tzinfo=timezone.utc),
                published_version="5ebece4b69dd593d82fe2020",
                entrance_exam_id=None,
                days_early_for_beta=None,
                sections=generate_sections(course_key, [2, 2]),
                self_paced=False,
                course_visibility=CourseVisibility.PUBLIC
            )
            replace_course_outline(outline)

        # Users
        cls.global_staff = UserFactory.create(
            username='global_staff',
            email='global_staff@example.com',
            is_staff=True,
            password='global_staff_pass',
        )
        cls.course_instructor = UserFactory.create(
            username='course_instructor',
            email='instructor@example.com',
            is_staff=False,
            password='course_instructor_pass',
        )
        cls.course_staff = UserFactory.create(
            username='course_staff',
            email='course_staff@example.com',
            is_staff=False,
            password='course_staff_pass',
        )
        cls.student = UserFactory.create(
            username='student',
            email='student@example.com',
            is_staff=False,
            password='student_pass',
        )

        # Roles
        CourseInstructorRole(cls.course_key).add_users(cls.course_instructor)
        CourseStaffRole(cls.course_key).add_users(cls.course_staff)

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org=self.course_key.org, number=self.course_key.course, run=self.course_key.run, display_name="Test Course"
        )
        self.not_staff_course = CourseFactory.create(
            org=self.not_staff_course_key.org,
            number=self.not_staff_course_key.course,
            run=self.not_staff_course_key.run,
            display_name="Test Course",
        )
        self.client = APIClient()

    def test_global_staff(self):
        """Global staff can successfuly masquerade in both courses."""
        self.client.login(username='global_staff', password='global_staff_pass')
        for course_key in [self.course_key, self.not_staff_course_key]:
            result = self.client.get(outline_url(course_key), {'user': 'student'})
            assert result.status_code == 200
            assert result.data['username'] == 'student'

    @ddt.data(
        ('course_instructor', 'course_instructor_pass'),
        ('course_staff', 'course_staff_pass'),
    )
    @ddt.unpack
    def test_course_staff(self, username, password):
        """Course Instructors/Staff can only masquerade for their own course."""
        self.client.login(username=username, password=password)
        our_course_url = outline_url(self.course_key)
        our_course_as_student = self.client.get(our_course_url, {'user': 'student'})
        assert our_course_as_student.status_code == 200
        assert our_course_as_student.data['username'] == 'student'

        our_course_as_self = self.client.get(our_course_url)
        assert our_course_as_self.status_code == 200
        assert our_course_as_self.data['username'] == username

        our_course_as_anonymous = self.client.get(our_course_url, {'user': ''})
        assert our_course_as_anonymous.status_code == 200
        assert our_course_as_anonymous.data['username'] == ''

        our_course_as_missing_user = self.client.get(our_course_url, {'user': 'idontexist'})
        assert our_course_as_missing_user.status_code == 404

        # No permission to masquerade in the course we're not a staff member of.
        other_course_as_student = self.client.get(
            outline_url(self.not_staff_course_key), {'user': 'student'}
        )
        assert other_course_as_student.status_code == 403

    def test_student(self):
        """Students have no ability to masquerade."""
        self.client.login(username='student', password='student_pass')
        course_url = outline_url(self.course_key)

        # No query param should net us the result as ourself
        course_as_self_implicit = self.client.get(course_url)
        assert course_as_self_implicit.status_code == 200
        assert course_as_self_implicit.data['username'] == 'student'

        # We should be allowed to explicitly put ourselves here...
        course_as_self_implicit = self.client.get(course_url, {'user': 'student'})
        assert course_as_self_implicit.status_code == 200
        assert course_as_self_implicit.data['username'] == 'student'

        # Any attempt to masquerade as anyone else (including anonymous users or
        # non-existent users) results in a 403.
        for username in ['idontexist', '', 'course_staff', 'global_staff']:
            masq_attempt_result = self.client.get(course_url, {'user': username})
            assert masq_attempt_result.status_code == 403


@ddt.ddt
@skip_unless_lms
class CourseOutlineViewMasqueradingTest(MasqueradeMixin, CacheIsolationTestCase, ModuleStoreTestCase):
    """
    Tests permissions of session masquerading.
    """
    @classmethod
    def setUpTestData(cls):
        """Set up the basic course outline and our users."""
        super().setUpTestData()

        overview = CourseOverviewFactory()
        cls.course_key = overview.id

        outline = CourseOutlineData(
            course_key=cls.course_key,
            title="Views Test Course!",
            published_at=datetime(2020, 5, 20, tzinfo=timezone.utc),
            published_version="5ebece4b69dd593d82fe2020",
            entrance_exam_id=None,
            days_early_for_beta=None,
            sections=generate_sections(cls.course_key, [2, 2]),
            self_paced=False,
            course_visibility=CourseVisibility.PUBLIC
        )
        replace_course_outline(outline)

        # Users
        cls.staff = UserFactory(is_staff=True, password='test')
        cls.student = UserFactory(username='student')
        UserFactory(username='student2')

        CourseEnrollment.enroll(cls.student, cls.course_key)

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(
            org=self.course_key.org,
            number=self.course_key.course,
            run=self.course_key.run,
            display_name="Test Course"
        )
        self.client.login(username=self.staff.username, password='test')

    def test_masquerading_works(self):
        """Confirm that session masquerading works as expected."""
        self.update_masquerade(course_id=self.course_key, username='student')
        result = self.client.get(outline_url(self.course_key))
        assert result.status_code == 200
        assert result.data['username'] == 'student'

    def test_target_user_takes_precedence(self):
        """Specifying a user should override any masquerading."""
        self.update_masquerade(course_id=self.course_key, username='student')
        result = self.client.get(outline_url(self.course_key), {'user': 'student2'})
        assert result.status_code == 200
        assert result.data['username'] == 'student2'


def outline_url(course_key):
    """Helper: Course outline URL for a given course key."""
    return f'/api/learning_sequences/v1/course_outline/{course_key}'
