"""
Tests student admin.py
"""


import datetime
import json
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import ddt
import pytest

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.forms import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.timezone import now
from edx_toggles.toggles.testutils import override_waffle_switch

from common.djangoapps.student.admin import (  # lint-amnesty, pylint: disable=line-too-long
    COURSE_ENROLLMENT_ADMIN_SWITCH,
    AllowedAuthUserForm,
    CourseEnrollmentForm,
    UserAdmin
)
from common.djangoapps.student.models import AllowedAuthUser, CourseEnrollment, LoginFailures
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory, UserProfileFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.site_configuration.tests.mixins import SiteMixin
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


class AdminCourseRolesPageTest(SharedModuleStoreTestCase):
    """Test the django admin course roles form saving data in db.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(org='edx')

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(is_staff=True, is_superuser=True)
        self.user.save()

    def test_save_valid_data(self):

        data = {
            'course_id': str(self.course.id),
            'role': 'finance_admin',
            'org': 'edx',
            'email': self.user.email
        }

        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

        # # adding new role from django admin page
        response = self.client.post(reverse('admin:student_courseaccessrole_add'), data=data)
        self.assertRedirects(response, reverse('admin:student_courseaccessrole_changelist'))

        response = self.client.get(reverse('admin:student_courseaccessrole_changelist'))
        self.assertContains(response, 'Select course access role to change')
        self.assertContains(response, 'Add course access role')
        self.assertContains(response, 'finance_admin')
        self.assertContains(response, str(self.course.id))
        self.assertContains(response, '1 course access role')

        #try adding with same information raise error.
        response = self.client.post(reverse('admin:student_courseaccessrole_add'), data=data)
        self.assertContains(response, 'Duplicate')

    def test_save_without_org_and_course_data(self):

        data = {
            'role': 'staff',
            'email': self.user.email,
            'course_id': str(self.course.id)
        }

        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

        # # adding new role from django admin page
        response = self.client.post(reverse('admin:student_courseaccessrole_add'), data=data)
        self.assertRedirects(response, reverse('admin:student_courseaccessrole_changelist'))

        response = self.client.get(reverse('admin:student_courseaccessrole_changelist'))
        self.assertContains(response, 'staff')
        self.assertContains(response, '1 course access role')

    def test_save_with_course_only(self):

        data = {
            'role': 'beta_testers',
            'email': self.user.email,

        }

        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

        # # adding new role from django admin page
        response = self.client.post(reverse('admin:student_courseaccessrole_add'), data=data)
        self.assertRedirects(response, reverse('admin:student_courseaccessrole_changelist'))

        response = self.client.get(reverse('admin:student_courseaccessrole_changelist'))
        self.assertContains(response, 'beta_testers')
        self.assertContains(response, '1 course access role')

    def test_save_with_org_only(self):

        data = {
            'role': 'beta_testers',
            'email': self.user.email,
            'org': 'myorg'

        }

        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

        # # adding new role from django admin page
        response = self.client.post(reverse('admin:student_courseaccessrole_add'), data=data)
        self.assertRedirects(response, reverse('admin:student_courseaccessrole_changelist'))

        response = self.client.get(reverse('admin:student_courseaccessrole_changelist'))
        self.assertContains(response, 'myorg')
        self.assertContains(response, '1 course access role')

    def test_save_with_invalid_course(self):

        course = 'no/edx/course'
        email = "invalid@email.com"
        data = {
            'course_id': course,
            'role': 'finance_admin',
            'org': 'edx',
            'email': email
        }

        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

        # Adding new role with invalid data
        response = self.client.post(reverse('admin:student_courseaccessrole_add'), data=data)
        self.assertContains(
            response,
            'Course not found. Entered course id was: &quot;{}&quot;.'.format(
                course
            )
        )

        self.assertContains(
            response,
            "Email does not exist. Could not find {}. Please re-enter email address".format(
                email
            )
        )

    def test_save_valid_course_invalid_org(self):

        data = {
            'course_id': str(self.course.id),
            'role': 'finance_admin',
            'org': 'edxxx',
            'email': self.user.email
        }

        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

        # # adding new role from django admin page
        response = self.client.post(reverse('admin:student_courseaccessrole_add'), data=data)
        self.assertContains(
            response,
            'Org name {} is not valid. Valid name is {}.'.format(
                'edxxx', 'edx'
            )
        )


class AdminUserPageTest(TestCase):
    """
    Unit tests for the UserAdmin view.
    """
    def setUp(self):
        super().setUp()
        self.admin = UserAdmin(User, AdminSite())

    def test_username_is_writable_for_user_creation(self):
        """
        Ensures that the username is not readonly, when admin creates new user.
        """
        request = Mock()
        assert 'username' not in self.admin.get_readonly_fields(request)

    def test_username_is_readonly_for_user(self):
        """
        Ensures that the username field is readonly, when admin open user which already exists.

        This hook used for skip Django validation in the `auth_user_change` view.

        Changing the username is still possible using the database or from the model directly.

        However, changing the username might cause issues with the logs and/or the forum service since it
        stores the username in a different database.
        """
        request = Mock()
        user = Mock()
        assert 'username' in self.admin.get_readonly_fields(request, user)


@ddt.ddt
class CourseEnrollmentAdminTest(SharedModuleStoreTestCase):
    """
    Unit tests for the CourseEnrollmentAdmin view.
    """
    ADMIN_URLS = (
        ('get', reverse('admin:student_courseenrollment_add')),
        ('get', reverse('admin:student_courseenrollment_changelist')),
        ('get', reverse('admin:student_courseenrollment_change', args=(1,))),
        ('get', reverse('admin:student_courseenrollment_delete', args=(1,))),
        ('post', reverse('admin:student_courseenrollment_add')),
        ('post', reverse('admin:student_courseenrollment_changelist')),
        ('post', reverse('admin:student_courseenrollment_change', args=(1,))),
        ('post', reverse('admin:student_courseenrollment_delete', args=(1,))),
    )

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(is_staff=True, is_superuser=True)
        self.course = CourseFactory()
        self.course_enrollment = CourseEnrollmentFactory(
            user=self.user,
            course_id=self.course.id,  # pylint: disable=no-member
        )
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)

    @ddt.data(*ADMIN_URLS)
    @ddt.unpack
    def test_view_disabled(self, method, url):
        """
        All CourseEnrollmentAdmin views are disabled by default.
        """
        response = getattr(self.client, method)(url)
        assert response.status_code == 403

    @ddt.data(*ADMIN_URLS)
    @ddt.unpack
    def test_view_enabled(self, method, url):
        """
        Ensure CourseEnrollmentAdmin views can be enabled with the waffle switch.
        """
        with override_waffle_switch(COURSE_ENROLLMENT_ADMIN_SWITCH, active=True):
            response = getattr(self.client, method)(url)
        assert response.status_code == 200

    def test_username_exact_match(self):
        """
        Ensure that course enrollment searches return exact matches on username first.
        """
        user2 = UserFactory.create(username=f'aaa_{self.user.username}')
        CourseEnrollmentFactory(
            user=user2,
            course_id=self.course.id,  # pylint: disable=no-member
        )
        search_url = '{}?q={}'.format(reverse('admin:student_courseenrollment_changelist'), self.user.username)
        with override_waffle_switch(COURSE_ENROLLMENT_ADMIN_SWITCH, active=True):
            response = self.client.get(search_url)
        assert response.status_code == 200

        # context['results'] is an array of arrays of HTML <td> elements to be rendered
        assert len(response.context['results']) == 2
        for idx, username in enumerate([self.user.username, user2.username]):
            # Locate the <td> column containing the username
            user_field = next(col for col in response.context['results'][idx] if "field-user" in col)
            assert username in user_field

    def test_save_toggle_active(self):
        """
        Edit a CourseEnrollment to toggle its is_active checkbox, save it and verify that it was toggled.
        When the form is saved, Django uses a QueryDict object which is immutable and needs special treatment.
        This test implicitly verifies that the POST parameters are handled correctly.
        """
        # is_active will change from True to False
        assert self.course_enrollment.is_active
        data = {
            'user': str(self.course_enrollment.user.id),
            'course': str(self.course_enrollment.course.id),
            'is_active': 'false',
            'mode': self.course_enrollment.mode,
        }

        with override_waffle_switch(COURSE_ENROLLMENT_ADMIN_SWITCH, active=True):
            response = self.client.post(
                reverse('admin:student_courseenrollment_change', args=(self.course_enrollment.id, )),
                data=data,
            )
        assert response.status_code == 302

        self.course_enrollment.refresh_from_db()
        assert not self.course_enrollment.is_active

    def test_save_invalid_course_id(self):
        """
        Send an invalid course ID instead of "org.0/course_0/Run_0" when saving, and verify that it fails.
        """
        data = {
            'user': str(self.course_enrollment.user.id),
            'course': 'invalid-course-id',
            'is_active': 'true',
            'mode': self.course_enrollment.mode,
        }

        with override_waffle_switch(COURSE_ENROLLMENT_ADMIN_SWITCH, active=True):
            with pytest.raises(ValidationError):
                self.client.post(
                    reverse('admin:student_courseenrollment_change', args=(self.course_enrollment.id, )),
                    data=data,
                )


@ddt.ddt
class LoginFailuresAdminTest(TestCase):
    """Test Login Failures Admin."""

    @classmethod
    def setUpClass(cls):
        """Setup class"""
        super().setUpClass()
        cls.TEST_PASSWORD = 'Password1234'
        cls.user = UserFactory.create(username='§', password=cls.TEST_PASSWORD, is_staff=True, is_superuser=True)
        cls.user.save()

    def setUp(self):
        """Setup."""
        super().setUp()
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        self.user2 = UserFactory.create(username='Zażółć gęślą jaźń')
        self.user_lockout_until = datetime.datetime.now(ZoneInfo("UTC"))
        LoginFailures.objects.create(user=self.user, failure_count=10, lockout_until=self.user_lockout_until)
        LoginFailures.objects.create(user=self.user2, failure_count=2)

    def tearDown(self):
        """Tear Down."""
        super().tearDown()
        LoginFailures.objects.all().delete()

    def test_unicode_username(self):
        """
        Test if `__str__` method behaves correctly for unicode username.
        It shouldn't raise `TypeError`.
        """
        assert str(LoginFailures.objects.get(user=self.user)) == f'§: 10 - {self.user_lockout_until.isoformat()}'
        assert str(LoginFailures.objects.get(user=self.user2)) == 'Zażółć gęślą jaźń: 2 - -'

    @override_settings(FEATURES={'ENABLE_MAX_FAILED_LOGIN_ATTEMPTS': True})
    def test_feature_enabled(self):
        url = reverse('admin:student_loginfailures_changelist')
        response = self.client.get(url)
        assert response.status_code == 200

    @ddt.data(
        reverse('admin:student_loginfailures_changelist'),
        reverse('admin:student_loginfailures_add'),
        reverse('admin:student_loginfailures_change', args=(1,)),
        reverse('admin:student_loginfailures_delete', args=(1,)),
    )
    def test_feature_disabled(self, url):
        """Test if feature is disabled there's no access to the admin module."""
        response = self.client.get(url)
        assert response.status_code == 403
        response = self.client.post(url)
        assert response.status_code == 403

    @override_settings(FEATURES={'ENABLE_MAX_FAILED_LOGIN_ATTEMPTS': True})
    def test_unlock_student_accounts(self):
        """Test batch unlock student accounts."""
        url = reverse('admin:student_loginfailures_changelist')
        self.client.post(
            url,
            data={
                'action': 'unlock_student_accounts',
                '_selected_action': [str(o.pk) for o in LoginFailures.objects.all()]
            },
            follow=True
        )
        count = LoginFailures.objects.count()
        assert count == 0

    @override_settings(FEATURES={'ENABLE_MAX_FAILED_LOGIN_ATTEMPTS': True})
    def test_unlock_account(self):
        """Test unlock single student account."""
        url = reverse('admin:student_loginfailures_change', args=(1, ))
        start_count = LoginFailures.objects.count()
        self.client.post(
            url,
            data={'_unlock': 1}
        )
        count = LoginFailures.objects.count()
        assert count == (start_count - 1)


class CourseEnrollmentAdminFormTest(SharedModuleStoreTestCase):
    """
    Unit test for CourseEnrollment admin ModelForm.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseOverviewFactory(start=now())

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()

    def test_admin_model_form_create(self):
        """
        Test CourseEnrollmentAdminForm creation.
        """
        assert CourseEnrollment.objects.count() == 0

        form = CourseEnrollmentForm({
            'user': self.user.id,
            'course': str(self.course.id),
            'is_active': True,
            'mode': 'audit',
        })
        assert form.is_valid()
        enrollment = form.save()
        assert CourseEnrollment.objects.count() == 1
        assert CourseEnrollment.objects.first() == enrollment

    def test_admin_model_form_update(self):
        """
        Test CourseEnrollmentAdminForm update.
        """
        enrollment = CourseEnrollment.get_or_create_enrollment(self.user, self.course.id)
        count = CourseEnrollment.objects.count()
        form = CourseEnrollmentForm({
            'user': self.user.id,
            'course': str(self.course.id),
            'is_active': False,
            'mode': 'audit'},
            instance=enrollment
        )
        assert form.is_valid()
        course_enrollment = form.save()
        assert count == CourseEnrollment.objects.count()
        assert not course_enrollment.is_active
        assert enrollment.id == course_enrollment.id


class AllowedAuthUserFormTest(SiteMixin, TestCase):
    """
    Unit test for AllowedAuthUserAdmin's ModelForm.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.email_domain_name = "dummy.com"
        cls.email_with_wrong_domain = "dummy@example.com"
        cls.valid_email = f"dummy@{cls.email_domain_name}"
        cls.other_valid_email = f"dummy1@{cls.email_domain_name}"
        UserFactory(email=cls.valid_email)
        UserFactory(email=cls.email_with_wrong_domain)

    def _update_site_configuration(self):
        """ Updates the site's configuration """
        self.site.configuration.site_values = {'THIRD_PARTY_AUTH_ONLY_DOMAIN': self.email_domain_name}
        self.site.configuration.save()

    def _assert_form(self, site, email, is_valid_form=False):
        """
        Asserts the form and returns the error if its not valid and instance if its valid
        """
        error = ''
        instance = None
        form = AllowedAuthUserForm({'site': site.id, 'email': email})
        if is_valid_form:
            assert form.is_valid()
            instance = form.save()
        else:
            assert not form.is_valid()
            error = form.errors['email'][0]
        return error, instance

    def test_form_with_invalid_site_configuration(self):
        """
        Test form with wrong site's configuration.
        """
        error, _ = self._assert_form(self.site, self.valid_email)
        assert error == "Please add a key/value 'THIRD_PARTY_AUTH_ONLY_DOMAIN/{site_email_domain}'" \
                        " in SiteConfiguration model's site_values field."

    def test_form_with_invalid_domain_name(self):
        """
        Test form with email which has wrong email domain.
        """
        self._update_site_configuration()
        error, _ = self._assert_form(self.site, self.email_with_wrong_domain)
        assert error == f"Email doesn't have {self.email_domain_name} domain name."

    def test_form_with_invalid_user(self):
        """
        Test form with an email which is not associated with any user.
        """
        self._update_site_configuration()
        error, _ = self._assert_form(self.site, self.other_valid_email)
        assert error == "User with this email doesn't exist in system."

    def test_form_creation(self):
        """
        Test AllowedAuthUserForm creation.
        """
        self._update_site_configuration()
        _, allowed_auth_user = self._assert_form(self.site, self.valid_email, is_valid_form=True)
        db_allowed_auth_user = AllowedAuthUser.objects.all().first()
        assert db_allowed_auth_user.site.id == allowed_auth_user.site.id
        assert db_allowed_auth_user.email == allowed_auth_user.email

    def test_form_update(self):
        """
        Test AllowedAuthUserForm update.
        """
        self._update_site_configuration()
        UserFactory(email=self.other_valid_email)
        _, allowed_auth_user = self._assert_form(self.site, self.valid_email, is_valid_form=True)
        assert AllowedAuthUser.objects.all().count() == 1

        # update the object with new instance.
        form = AllowedAuthUserForm({'site': self.site.id, 'email': self.other_valid_email}, instance=allowed_auth_user)
        assert form.is_valid()
        form.save()

        db_allowed_auth_user = AllowedAuthUser.objects.all().first()
        assert AllowedAuthUser.objects.all().count() == 1
        assert db_allowed_auth_user.email == self.other_valid_email


@ddt.ddt
class TestUserProfileAutocompleteAdmin(TestCase):
    """Tests for language and country autocomplete in UserProfile inline form via Django admin."""

    def setUp(self):
        super().setUp()
        self.staff_user = UserFactory(is_staff=True)
        self.staff_user.set_password('test')
        self.staff_user.save()

        self.non_staff_user = UserFactory(is_staff=False)
        self.non_staff_user.set_password('test')
        self.non_staff_user.save()

        self.client.login(username=self.staff_user.username, password='test')

        self.language_url = reverse('admin:language-autocomplete')
        self.country_url = reverse('admin:country-autocomplete')

        user1 = UserFactory()
        user1.set_password('test')
        user1.save()
        UserProfileFactory(user=user1, language='English', country='PK')

        user2 = UserFactory()
        user2.set_password('test')
        user2.save()
        UserProfileFactory(user=user2, language='French', country='GB')

        user3 = UserFactory()
        user3.set_password('test')
        user3.save()
        UserProfileFactory(user=user3, language='German', country='US')

    def test_language_autocomplete_returns_expected_result(self):
        """Verify language autocomplete returns expected filtered results."""
        profile = UserProfileFactory(user=self.staff_user, language='Esperanto')

        response = self.client.get(self.language_url)
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.content.decode('utf-8'))
        self.assertTrue(
            any('Esperanto' in item['text'] for item in data['results']),
            f"Esperanto not found in: {data['results']}"
        )

        profile.language = 'French'
        profile.save()

        response = self.client.get(f'{self.language_url}?q=Fren')
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.content.decode('utf-8'))
        self.assertTrue(
            any('French' in item['text'] for item in data['results']),
            f"French not found in: {data['results']}"
        )

    def test_country_autocomplete_returns_expected_result(self):
        """Verify country autocomplete returns expected filtered results."""
        profile = UserProfileFactory(user=self.staff_user, country='SE')

        response = self.client.get(self.country_url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content.decode('utf-8'))
        self.assertTrue(
            any('Sweden' in item['text'] for item in data['results']),
            f"Sweden not found in: {data['results']}"
        )

        profile.country = 'JP'
        profile.save()

        response = self.client.get(f'{self.country_url}?q=Japan')
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.content.decode('utf-8'))
        self.assertTrue(
            any('Japan' in item['text'] for item in data['results']),
            f"Japan not found in: {data['results']}"
        )

    @ddt.data('eng', 'fren', 'GER')
    def test_language_autocomplete_filters_correctly(self, term):
        response = self.client.get(f'{self.language_url}?q={term}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(any(term.lower() in item['text'].lower() for item in data['results']))

    def test_language_autocomplete_returns_empty_on_no_match(self):
        response = self.client.get(f'{self.language_url}?q=not-a-lang')
        self.assertEqual(json.loads(response.content)['results'], [])

    @ddt.data('United', 'Kingdom', 'Pakistan')
    def test_country_autocomplete_filters_correctly(self, term):
        response = self.client.get(f'{self.country_url}?q={term}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(any(term.lower() in item['text'].lower() for item in data['results']))

    def test_country_autocomplete_returns_empty_on_gibberish(self):
        response = self.client.get(f'{self.country_url}?q=asdfghjkl')
        self.assertEqual(json.loads(response.content)['results'], [])

    def test_admin_inline_autocomplete_urls_render(self):
        admin = UserFactory(is_staff=True, is_superuser=True)
        admin.set_password('test')
        admin.save()

        user = UserFactory()
        user.set_password('test')
        user.save()
        self.client.login(username=admin.username, password='test')  # re-login as admin

        response = self.client.get(reverse('admin:auth_user_change', args=[user.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.language_url)
        self.assertContains(response, self.country_url)

    def test_language_autocomplete_blocks_non_staff(self):
        self.client.logout()
        self.client.login(username=self.non_staff_user.username, password='test')
        response = self.client.get(f'{self.language_url}?q=english')
        data = json.loads(response.content)
        self.assertEqual(data['results'], [])

    def test_country_autocomplete_blocks_non_staff(self):
        self.client.logout()
        self.client.login(username=self.non_staff_user.username, password='test')
        response = self.client.get(f'{self.country_url}?q=pakistan')
        data = json.loads(response.content)
        self.assertEqual(data['results'], [])

    def test_language_autocomplete_blocks_anonymous_user(self):
        """Ensure anonymous user gets blocked or redirected."""
        self.client.logout()
        response = self.client.get(f'{self.language_url}?q=English')
        self.assertIn(response.status_code, [302, 403])

    def test_country_autocomplete_blocks_anonymous_user(self):
        """Ensure anonymous user gets blocked or redirected."""
        self.client.logout()
        response = self.client.get(f'{self.country_url}?q=Pakistan')
        self.assertIn(response.status_code, [302, 403])

    def test_language_autocomplete_status_for_non_staff(self):
        self.client.logout()
        self.client.login(username=self.non_staff_user.username, password='test')
        response = self.client.get(f'{self.language_url}?q=English')
        self.assertEqual(response.status_code, 200)  # still 200, but empty results expected
        self.assertEqual(json.loads(response.content)['results'], [])

    def test_unknown_autocomplete_path_404s(self):
        logged_in = self.client.login(username=self.staff_user.username, password='test')
        assert logged_in, "Login failed — test user not authenticated"

        response = self.client.get('/admin/myapp/mymodel/fake-autocomplete/')
        self.assertEqual(response.status_code, 404)
