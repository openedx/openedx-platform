"""Provides factories for student models."""


from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import factory
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.test.client import RequestFactory
from factory.django import DjangoModelFactory
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import (
    AccountRecovery,
    CourseAccessRole,
    CourseEnrollment,
    CourseEnrollmentAllowed,
    CourseEnrollmentAttribute,
    CourseEnrollmentCelebration,
    PendingEmailChange,
    Registration,
    User,
    UserAttribute,
    UserProfile,
    UserStanding
)
from common.djangoapps.student.roles import GlobalStaff
from common.djangoapps.student.roles import CourseBetaTesterRole
from common.djangoapps.student.roles import CourseInstructorRole
from common.djangoapps.student.roles import CourseStaffRole
from common.djangoapps.student.roles import OrgInstructorRole
from common.djangoapps.student.roles import OrgStaffRole
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory

TEST_PASSWORD = 'Password1234'


class GroupFactory(DjangoModelFactory):  # lint-amnesty, pylint: disable=missing-class-docstring
    class Meta:
        model = Group
        django_get_or_create = ('name', )

    name = factory.Sequence('group{}'.format)


class UserStandingFactory(DjangoModelFactory):  # lint-amnesty, pylint: disable=missing-class-docstring
    class Meta:
        model = UserStanding

    user = None
    account_status = None
    changed_by = None


class UserProfileFactory(DjangoModelFactory):  # lint-amnesty, pylint: disable=missing-class-docstring
    class Meta:
        model = UserProfile
        django_get_or_create = ('user', )

    user = None
    name = factory.LazyAttribute('{0.user.first_name} {0.user.last_name}'.format)
    level_of_education = None
    gender = 'm'
    mailing_address = None
    goals = 'Learn a lot'


class RegistrationFactory(DjangoModelFactory):  # lint-amnesty, pylint: disable=missing-class-docstring
    class Meta:
        model = Registration

    user = None
    activation_key = str(uuid4().hex)


class UserFactory(DjangoModelFactory):  # lint-amnesty, pylint: disable=missing-class-docstring
    class Meta:
        model = User
        django_get_or_create = ('email', 'username')

    _DEFAULT_PASSWORD = 'Password1234'

    username = factory.Sequence('robot{}'.format)
    email = factory.Sequence('robot+test+{}@edx.org'.format)
    password = factory.PostGenerationMethodCall('set_password', _DEFAULT_PASSWORD)
    first_name = factory.Sequence('Robot{}'.format)
    last_name = 'Test'
    is_staff = False
    is_active = True
    is_superuser = False
    last_login = datetime(2012, 1, 1, tzinfo=ZoneInfo("UTC"))
    date_joined = datetime(2011, 1, 1, tzinfo=ZoneInfo("UTC"))

    @factory.post_generation
    def profile(obj, create, extracted, **kwargs):  # pylint: disable=unused-argument, missing-function-docstring
        if create:
            obj.save()
            return UserProfileFactory.create(user=obj, **kwargs)
        elif kwargs:
            raise Exception("Cannot build a user profile without saving the user")
        else:
            return None

    @factory.post_generation
    def groups(self, create, extracted, **kwargs):  # lint-amnesty, pylint: disable=missing-function-docstring, unused-argument
        if extracted is None:
            return

        if isinstance(extracted, str):
            extracted = [extracted]

        for group_name in extracted:
            self.groups.add(GroupFactory.simple_generate(create, name=group_name))  # lint-amnesty, pylint: disable=no-member


class UserAttributeFactory(DjangoModelFactory):  # lint-amnesty, pylint: disable=missing-class-docstring
    class Meta:
        model = UserAttribute

    user = factory.SubFactory(UserFactory)
    name = factory.Sequence('{}'.format)
    value = factory.Sequence('{}'.format)


class RequestFactoryNoCsrf(RequestFactory):
    """
    RequestFactory, which disables csrf checks.
    """
    def request(self, **kwargs):
        request = super().request(**kwargs)
        setattr(request, '_dont_enforce_csrf_checks', True)  # pylint: disable=literal-used-as-attribute
        return request


class AnonymousUserFactory(factory.Factory):
    class Meta:
        model = AnonymousUser


class AdminFactory(UserFactory):
    is_staff = True


class SuperuserFactory(UserFactory):
    is_superuser = True


class CourseEnrollmentFactory(DjangoModelFactory):  # lint-amnesty, pylint: disable=missing-class-docstring
    class Meta:
        model = CourseEnrollment

    user = factory.SubFactory(UserFactory)

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        manager = cls._get_manager(model_class)
        course_kwargs = {}
        for key in list(kwargs):
            if key.startswith('course__'):
                course_kwargs[key.split('__')[1]] = kwargs.pop(key)

        if 'course' not in kwargs:
            course_id = kwargs.get('course_id')
            course_overview = None
            if course_id is not None:
                # 'course_id' is not needed by the model when course is passed.
                # This arg used to be called course_id before we added the CourseOverview
                # foreign key constraint to CourseEnrollment.
                del kwargs['course_id']

                if isinstance(course_id, str):
                    course_id = CourseKey.from_string(course_id)
                    course_kwargs.setdefault('id', course_id)

                try:
                    course_overview = CourseOverview.get_from_id(course_id)
                except CourseOverview.DoesNotExist:
                    pass

            if course_overview is None:
                if 'id' not in course_kwargs and course_id:
                    course_kwargs['id'] = course_id

                course_overview = CourseOverviewFactory(**course_kwargs)
            kwargs['course'] = course_overview

        return manager.create(*args, **kwargs)


class CourseEnrollmentCelebrationFactory(DjangoModelFactory):
    class Meta:
        model = CourseEnrollmentCelebration

    enrollment = factory.SubFactory(CourseEnrollmentFactory)


class CourseEnrollmentAttributeFactory(DjangoModelFactory):
    class Meta:
        model = CourseEnrollmentAttribute

    enrollment = factory.SubFactory(CourseEnrollmentFactory)


class CourseAccessRoleFactory(DjangoModelFactory):  # lint-amnesty, pylint: disable=missing-class-docstring
    class Meta:
        model = CourseAccessRole

    user = factory.SubFactory(UserFactory)
    course_id = CourseKey.from_string('edX/toy/2012_Fall')
    role = 'TestRole'


class CourseEnrollmentAllowedFactory(DjangoModelFactory):  # lint-amnesty, pylint: disable=missing-class-docstring
    class Meta:
        model = CourseEnrollmentAllowed

    email = 'test@edx.org'
    course_id = CourseKey.from_string('edX/toy/2012_Fall')


class PendingEmailChangeFactory(DjangoModelFactory):
    """Factory for PendingEmailChange objects

    user: generated by UserFactory
    new_email: sequence of new+email+{}@edx.org
    activation_key: sequence of integers, padded to 30 characters
    """
    class Meta:
        model = PendingEmailChange

    user = factory.SubFactory(UserFactory)
    new_email = factory.Sequence('new+email+{}@edx.org'.format)
    activation_key = factory.Sequence('{:0<30d}'.format)


class ContentTypeFactory(DjangoModelFactory):
    class Meta:
        model = ContentType

    app_label = factory.Faker('app_name')


class PermissionFactory(DjangoModelFactory):  # lint-amnesty, pylint: disable=missing-class-docstring
    class Meta:
        model = Permission

    codename = factory.Faker('codename')
    content_type = factory.SubFactory(ContentTypeFactory)


class AccountRecoveryFactory(DjangoModelFactory):  # lint-amnesty, pylint: disable=missing-class-docstring
    class Meta:
        model = AccountRecovery
        django_get_or_create = ('user',)

    user = None
    secondary_email = factory.Sequence('robot+test+recovery+{}@edx.org'.format)
    is_active = True


class BetaTesterFactory(UserFactory):
    """
    Given a course Location, returns a User object with beta-tester
    permissions for `course`.
    """
    last_name = 'Beta-Tester'

    @factory.post_generation
    def course_key(self, _create, extracted, **kwargs):
        if extracted is None:
            raise ValueError('Must specify a CourseKey for a beta-tester user')
        CourseBetaTesterRole(extracted).add_users(self)


class GlobalStaffFactory(UserFactory):
    """
    Returns a User object with global staff access
    """
    last_name = 'GlobalStaff'

    @factory.post_generation
    def set_staff(self, _create, _extracted, **kwargs):
        GlobalStaff().add_users(self)


class InstructorFactory(UserFactory):
    """
    Given a course Location, returns a User object with instructor
    permissions for `course`.
    """
    last_name = 'Instructor'

    @factory.post_generation
    def course_key(self, _create, extracted, **kwargs):
        if extracted is None:
            raise ValueError('Must specify a CourseKey for a course instructor user')
        CourseInstructorRole(extracted).add_users(self)


class OrgInstructorFactory(UserFactory):
    """
    Given a course Location, returns a User object with org-instructor
    permissions for `course`.
    """
    last_name = 'Org-Instructor'

    @factory.post_generation
    def course_key(self, _create, extracted, **kwargs):
        if extracted is None:
            raise ValueError('Must specify a CourseKey for an org-instructor user')
        OrgInstructorRole(extracted.org).add_users(self)


class OrgStaffFactory(UserFactory):
    """
    Given a course Location, returns a User object with org-staff
    permissions for `course`.
    """
    last_name = 'Org-Staff'

    @factory.post_generation
    def course_key(self, _create, extracted, **kwargs):
        if extracted is None:
            raise ValueError('Must specify a CourseKey for an org-staff user')
        OrgStaffRole(extracted.org).add_users(self)


class StaffFactory(UserFactory):
    """
    Given a course Location, returns a User object with staff
    permissions for `course`.
    """
    last_name = 'Staff'

    @factory.post_generation
    def course_key(self, _create, extracted, **kwargs):
        if extracted is None:
            raise ValueError('Must specify a CourseKey for a course staff user')
        CourseStaffRole(extracted).add_users(self)
