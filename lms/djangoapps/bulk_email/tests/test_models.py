"""
Unit tests for bulk-email-related models.
"""

import datetime
from unittest.mock import Mock, patch  # pylint: disable=wrong-import-order

import ddt
import pytest
from dateutil.relativedelta import relativedelta
from django.core.management import call_command
from django.test import TestCase
from django.test.utils import override_settings
from opaque_keys.edx.keys import CourseKey
from pytz import UTC

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, StaffFactory, UserFactory
from lms.djangoapps.bulk_email.api import is_bulk_email_feature_enabled
from lms.djangoapps.bulk_email.models import (
    SEND_TO_COHORT,
    SEND_TO_STAFF,
    SEND_TO_TRACK,
    BulkEmailFlag,
    CourseAuthorization,
    CourseEmail,
    CourseEmailTemplate,
    CourseModeTarget,
    DisabledCourse,
    Optout,
    Target,
)
from lms.djangoapps.bulk_email.models_api import is_bulk_email_disabled_for_course
from lms.djangoapps.bulk_email.tests.factories import TargetFactory
from openedx.core.djangoapps.course_groups.models import CourseCohort
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # pylint: disable=wrong-import-order


@ddt.ddt
@patch('lms.djangoapps.bulk_email.models.html_to_text', Mock(return_value='Mocking CourseEmail.text_message', autospec=True))  # pylint: disable=line-too-long
class CourseEmailTest(ModuleStoreTestCase):
    """Test the CourseEmail model."""

    def test_creation(self):
        course_id = CourseKey.from_string('abc/123/doremi')
        sender = UserFactory.create()
        to_option = SEND_TO_STAFF
        subject = "dummy subject"
        html_message = "<html>dummy message</html>"
        email = CourseEmail.create(course_id, sender, [to_option], subject, html_message)
        assert email.course_id == course_id
        assert SEND_TO_STAFF in [target.target_type for target in email.targets.all()]
        assert email.subject == subject
        assert email.html_message == html_message
        assert email.sender == sender

    def test_creation_with_optional_attributes(self):
        course_id = CourseKey.from_string('abc/123/doremi')
        sender = UserFactory.create()
        to_option = SEND_TO_STAFF
        subject = "dummy subject"
        html_message = "<html>dummy message</html>"
        template_name = "branded_template"
        from_addr = "branded@branding.com"
        email = CourseEmail.create(
            course_id, sender, [to_option], subject, html_message, template_name=template_name, from_addr=from_addr
        )
        assert email.course_id == course_id
        assert email.targets.all()[0].target_type == SEND_TO_STAFF
        assert email.subject == subject
        assert email.html_message == html_message
        assert email.sender == sender
        assert email.template_name == template_name
        assert email.from_addr == from_addr

    def test_bad_to_option(self):
        course_id = CourseKey.from_string('abc/123/doremi')
        sender = UserFactory.create()
        to_option = "fake"
        subject = "dummy subject"
        html_message = "<html>dummy message</html>"
        with pytest.raises(ValueError):  # noqa: PT011
            CourseEmail.create(course_id, sender, to_option, subject, html_message)

    @ddt.data(
        datetime.datetime(1999, 1, 1, tzinfo=UTC),
        datetime.datetime(datetime.MAXYEAR, 1, 1, tzinfo=UTC),
    )
    def test_track_target(self, expiration_datetime):
        """
        Tests that emails can be sent to a specific track. Also checks that
         emails can be sent to an expired track (EDUCATOR-364)
        """
        course = CourseFactory.create()
        course_id = course.id
        sender = UserFactory.create()
        to_option = 'track:test'
        subject = "dummy subject"
        html_message = "<html>dummy message</html>"
        CourseMode.objects.create(
            mode_slug='test',
            mode_display_name='Test',
            course_id=course_id,
            expiration_datetime=expiration_datetime,
        )
        email = CourseEmail.create(course_id, sender, [to_option], subject, html_message)
        assert len(email.targets.all()) == 1
        target = email.targets.all()[0]
        assert target.target_type == SEND_TO_TRACK
        assert target.short_display() == 'track-test'
        assert target.long_display() == 'Course mode: Test, Currency: usd'

    @ddt.data(
        CourseMode.AUDIT,
        CourseMode.HONOR,
    )
    def test_track_target_with_free_mode(self, free_mode):
        """
        Tests that when emails are sent to a free track the track display
        should not contain currency.
        """
        course = CourseFactory.create()
        mode_display_name = free_mode.capitalize
        course_id = course.id
        sender = UserFactory.create()
        to_option = f'track:{free_mode}'
        subject = "dummy subject"
        html_message = "<html>dummy message</html>"
        CourseMode.objects.create(
            mode_slug=free_mode,
            mode_display_name=mode_display_name,
            course_id=course_id,
        )

        email = CourseEmail.create(course_id, sender, [to_option], subject, html_message)
        assert len(email.targets.all()) == 1
        target = email.targets.all()[0]
        assert target.target_type == SEND_TO_TRACK
        assert target.short_display() == f'track-{free_mode}'
        assert target.long_display() == f'Course mode: {mode_display_name}'

    def test_cohort_target(self):
        course_id = CourseKey.from_string('abc/123/doremi')
        sender = UserFactory.create()
        to_option = 'cohort:test cohort'
        subject = "dummy subject"
        html_message = "<html>dummy message</html>"
        CourseCohort.create(cohort_name='test cohort', course_id=course_id)
        email = CourseEmail.create(course_id, sender, [to_option], subject, html_message)
        assert len(email.targets.all()) == 1
        target = email.targets.all()[0]
        assert target.target_type == SEND_TO_COHORT
        assert target.short_display() == 'cohort-test cohort'
        assert target.long_display() == 'Cohort: test cohort'


class OptoutTest(TestCase):  # pylint: disable=missing-class-docstring
    def test_is_user_opted_out_for_course(self):
        user = UserFactory.create()
        course_id = CourseKey.from_string('abc/123/doremi')

        assert not Optout.is_user_opted_out_for_course(user, course_id)

        Optout.objects.create(
            user=user,
            course_id=course_id,
        )

        assert Optout.is_user_opted_out_for_course(user, course_id)


class NoCourseEmailTemplateTest(TestCase):
    """Test the CourseEmailTemplate model without loading the template data."""

    def test_get_missing_template(self):
        with pytest.raises(CourseEmailTemplate.DoesNotExist):
            CourseEmailTemplate.get_template()


class CourseEmailTemplateTest(TestCase):
    """Test the CourseEmailTemplate model."""

    def setUp(self):
        super().setUp()

        # load initial content (since we don't run migrations as part of tests):
        call_command("loaddata", "course_email_template.json")

    def _get_sample_plain_context(self):
        """Provide sample context sufficient for rendering plaintext template"""
        context = {
            'course_title': "Bogus Course Title",
            'course_url': "/location/of/course/url",
            'email_settings_url': "/location/of/email/settings/url",
            'platform_name': 'edX',
            'email': 'your-email@test.com',
            'unsubscribe_link': '/bulk_email/email/optout/dummy'
        }
        return context

    def _get_sample_html_context(self):
        """Provide sample context sufficient for rendering HTML template"""
        context = self._get_sample_plain_context()
        context['course_image_url'] = "/location/of/course/image/url"
        return context

    def _add_xss_fields(self, context):
        """ Add fields to the context for XSS testing. """
        context['course_title'] = "<script>alert('Course Title!');</alert>"
        context['name'] = "<script>alert('Profile Name!');</alert>"
        # Must have user_id and course_id present in order to do keyword substitution
        context['user_id'] = 12345
        context['course_id'] = "course-v1:edx+100+1"
        return context

    def test_get_template(self):
        # Get the default template, which has name=None
        template = CourseEmailTemplate.get_template()
        assert template.html_template is not None
        assert template.plain_template is not None

    def test_get_branded_template(self):
        # Get a branded (non default) template and make sure we get what we expect
        template = CourseEmailTemplate.get_template(name="branded.template")
        assert template.html_template is not None
        assert template.plain_template is not None
        assert 'THIS IS A BRANDED HTML TEMPLATE' in template.html_template
        assert 'THIS IS A BRANDED TEXT TEMPLATE' in template.plain_template

    def test_render_html_without_context(self):
        template = CourseEmailTemplate.get_template()
        base_context = self._get_sample_html_context()
        for keyname in base_context:
            context = dict(base_context)
            del context[keyname]
            with pytest.raises(KeyError):
                template.render_htmltext("My new html text.", context)

    def test_render_plaintext_without_context(self):
        template = CourseEmailTemplate.get_template()
        base_context = self._get_sample_plain_context()
        for keyname in base_context:
            context = dict(base_context)
            del context[keyname]
            with pytest.raises(KeyError):
                template.render_plaintext("My new plain text.", context)

    def test_render_html(self):
        template = CourseEmailTemplate.get_template()
        context = self._get_sample_html_context()
        template.render_htmltext("My new html text.", context)

    def test_render_html_xss(self):
        template = CourseEmailTemplate.get_template()
        context = self._add_xss_fields(self._get_sample_html_context())
        message = template.render_htmltext(
            "Dear %%USER_FULLNAME%%, thanks for enrolling in %%COURSE_DISPLAY_NAME%%.", context
        )
        assert '<script>' not in message
        assert '&lt;script&gt;alert(&#39;Course Title!&#39;);&lt;/alert&gt;' in message
        assert '&lt;script&gt;alert(&#39;Profile Name!&#39;);&lt;/alert&gt;' in message

    def test_render_plain(self):
        template = CourseEmailTemplate.get_template()
        context = self._get_sample_plain_context()
        template.render_plaintext("My new plain text.", context)

    def test_render_plain_no_escaping(self):
        template = CourseEmailTemplate.get_template()
        context = self._add_xss_fields(self._get_sample_plain_context())
        message = template.render_plaintext(
            "Dear %%USER_FULLNAME%%, thanks for enrolling in %%COURSE_DISPLAY_NAME%%.", context
        )
        assert '&lt;script&gt;' not in message
        assert context['course_title'] in message
        assert context['name'] in message


class CourseAuthorizationTest(TestCase):
    """Test the CourseAuthorization model."""

    def tearDown(self):
        super().tearDown()
        BulkEmailFlag.objects.all().delete()

    def test_creation_auth_on(self):
        BulkEmailFlag.objects.create(enabled=True, require_course_email_auth=True)
        course_id = CourseKey.from_string('abc/123/doremi')
        # Test that course is not authorized by default
        assert not is_bulk_email_feature_enabled(course_id)

        # Authorize
        cauth = CourseAuthorization(course_id=course_id, email_enabled=True)
        cauth.save()
        # Now, course should be authorized
        assert is_bulk_email_feature_enabled(course_id)
        assert str(cauth) == "Course 'abc/123/doremi': Instructor Email Enabled"

        # Unauthorize by explicitly setting email_enabled to False
        cauth.email_enabled = False
        cauth.save()
        # Test that course is now unauthorized
        assert not is_bulk_email_feature_enabled(course_id)
        assert str(cauth) == "Course 'abc/123/doremi': Instructor Email Not Enabled"

    def test_creation_auth_off(self):
        BulkEmailFlag.objects.create(enabled=True, require_course_email_auth=False)
        course_id = CourseKey.from_string('blahx/blah101/ehhhhhhh')
        # Test that course is authorized by default, since auth is turned off
        assert is_bulk_email_feature_enabled(course_id)

        # Use the admin interface to unauthorize the course
        cauth = CourseAuthorization(course_id=course_id, email_enabled=False)
        cauth.save()

        # Now, course should STILL be authorized!
        assert is_bulk_email_feature_enabled(course_id)


class DisabledCourseTest(TestCase):
    """
    Test DisabledCourse model and api
    """
    def tearDown(self):
        super().tearDown()
        BulkEmailFlag.objects.all().delete()

    def test_is_email_disabled_for_course(self):
        BulkEmailFlag.objects.create(enabled=True)
        course_id = CourseKey.from_string('abc/123/doremi')
        # Test that course is not disabled by default
        assert not is_bulk_email_disabled_for_course(course_id)

        # Disable the course
        disabled_course = DisabledCourse(course_id=course_id)
        disabled_course.save()
        # Course should be disabled
        assert is_bulk_email_disabled_for_course(course_id)


class TargetFilterTest(ModuleStoreTestCase):
    """
    Tests for the optional filtering of recipients from the results of the `get_users` method of the Target model.
    """

    def setUp(self):
        super().setUp()
        self.user1 = UserFactory(last_login=datetime.datetime.now())
        self.user2 = UserFactory(last_login=datetime.datetime.now() - relativedelta(months=2))
        self.user3 = UserFactory()
        self.course = CourseFactory()
        CourseEnrollmentFactory(
            is_active=True,
            mode='verified',
            course_id=self.course.id,
            user=self.user1
        )
        CourseEnrollmentFactory(
            is_active=True,
            mode='audit',
            course_id=self.course.id,
            user=self.user2
        )
        CourseEnrollmentFactory(
            is_active=False,
            mode='verified',
            course_id=self.course.id,
            user=self.user3
        )
        self.staff_user = StaffFactory.create(course_key=self.course.id)
        self.target = TargetFactory()

    @override_settings(BULK_COURSE_EMAIL_LAST_LOGIN_ELIGIBILITY_PERIOD=None)
    def test_target_no_last_login_eligibility(self):
        """
        Verifies the default behavior stays the same if the `BULK_COURSE_EMAIL_LAST_LOGIN_ELIGIBILITY_PERIOD` is not
        set.
        """
        result = self.target.get_users(self.course.id)

        assert result.count() == 2
        assert result.filter(id=self.user1.id).exists()
        assert result.filter(id=self.user2.id).exists()

    @override_settings(BULK_COURSE_EMAIL_LAST_LOGIN_ELIGIBILITY_PERIOD=1)
    def test_target_last_login_eligibility_set(self):
        """
        Verifies that users with a `login_date` beyond the treshold set according to the
        `BULK_COURSE_EMAIL_LAST_LOGIN_ELIGIBILITY_PERIOD` are excluded from the final results of the queryset returned
        callers of the `get_users` method.
        """
        result = self.target.get_users(self.course.id)

        assert result.count() == 1
        assert result.filter(id=self.user1.id).exists()

    def test_filtering_of_recipients_target_for_audit_track(self):
        """
        Verifies the default behavior.

        This test ensures that when the `BULK_COURSE_EMAIL_LAST_LOGIN_ELIGIBILITY_PERIOD`
        setting is not defined, all users enrolled in the course are included in the results.
        """
        target = Target.objects.create(target_type=SEND_TO_TRACK)
        course_mode = CourseMode.objects.create(
            mode_slug=CourseMode.AUDIT,
            mode_display_name=CourseMode.AUDIT.capitalize(),
            course_id=self.course.id,
        )
        course_mode_target = CourseModeTarget.objects.create(track=course_mode)
        target.coursemodetarget = course_mode_target
        result = target.get_users(self.course.id)

        assert result.count() == 1
        assert result.filter(id=self.user2.id).exists()

        # Ensure staff user is not included
        assert not result.filter(id=self.staff_user.id).exists()

    def test_filtering_of_recipients_target_for_staff(self):
        """
        Test filtering of recipients for a target of type SEND_TO_STAFF.

        This test verifies that only staff users are returned for the given target.
        It creates a target of type SEND_TO_STAFF and ensures that the correct users
        are retrieved.
        """
        self.target = TargetFactory(target_type=SEND_TO_STAFF)
        result = self.target.get_users(self.course.id)

        assert result.count() == 1
        assert result.filter(id=self.staff_user.id).exists()
