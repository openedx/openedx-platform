# pylint: disable=missing-docstring,unused-argument


import json
import logging

from config_models.models import ConfigurationModel
from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver

from django.utils.translation import gettext_noop
from jsonfield.fields import JSONField
from opaque_keys.edx.django.models import CourseKeyField
from edx_django_utils.cache import DEFAULT_REQUEST_CACHE
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.xmodule_django.models import NoneToEmptyManager
from openedx.core.lib.cache_utils import request_cached
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import GlobalStaff
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import ItemNotFoundError  # lint-amnesty, pylint: disable=wrong-import-order

FORUM_ROLE_ADMINISTRATOR = gettext_noop('Administrator')
FORUM_ROLE_MODERATOR = gettext_noop('Moderator')
FORUM_ROLE_GROUP_MODERATOR = gettext_noop('Group Moderator')
FORUM_ROLE_COMMUNITY_TA = gettext_noop('Community TA')
FORUM_ROLE_STUDENT = gettext_noop('Student')


@receiver(post_save, sender=CourseEnrollment)
def assign_default_role_on_enrollment(sender, instance, **kwargs):
    """
    Assign forum default role 'Student'
    """
    # The code below would remove all forum Roles from a user when they unenroll
    # from a course. Concerns were raised that it should apply only to students,
    # or that even the history of student roles is important for research
    # purposes. Since this was new functionality being added in this release,
    # I'm just going to comment it out for now and let the forums team deal with
    # implementing the right behavior.
    #
    # # We've unenrolled the student, so remove all roles for this course
    # if not instance.is_active:
    #     course_roles = list(Role.objects.filter(course_id=instance.course_id))
    #     instance.user.roles.remove(*course_roles)
    #     return

    # We've enrolled the student, so make sure they have the Student role
    assign_default_role(instance.course_id, instance.user)


def assign_default_role(course_id, user):
    """
    Assign forum default role 'Student' to user
    """
    assign_role(course_id, user, FORUM_ROLE_STUDENT)


def assign_role(course_id, user, rolename):
    """
    Assign forum role `rolename` to user
    """
    role, created = Role.objects.get_or_create(course_id=course_id, name=rolename)
    if created:
        logging.info(f"EDUCATOR-1635: Created role {role} for course {course_id}")
    user.roles.add(role)


class Role(models.Model):
    """
    Maps users to django_comment_client roles for a given course

    .. no_pii:
    """

    objects = NoneToEmptyManager()

    name = models.CharField(max_length=30, null=False, blank=False)
    users = models.ManyToManyField(User, related_name="roles")
    course_id = CourseKeyField(max_length=255, blank=True, db_index=True)

    class Meta:
        # use existing table that was originally created from lms.djangoapps.discussion.django_comment_client app
        db_table = 'django_comment_client_role'

    def __str__(self):
        return self.name + " for " + (str(self.course_id) if self.course_id else "all courses")

    # TODO the name of this method is a little bit confusing,
    # since it's one-off and doesn't handle inheritance later
    def inherit_permissions(self, role):
        """
        Make this role inherit permissions from the given role.
        Permissions are only added, not removed. Does not handle inheritance.
        """
        if role.course_id and role.course_id != self.course_id:
            logging.warning(
                "%s cannot inherit permissions from %s due to course_id inconsistency",
                self,
                role,
            )
        for per in role.permissions.all():
            self.add_permission(per)

    def add_permission(self, permission):
        self.permissions.add(Permission.objects.get_or_create(name=permission)[0])  # lint-amnesty, pylint: disable=no-member

    def has_permission(self, permission):
        """
        Returns True if this role has the given permission, False otherwise.
        """
        course = modulestore().get_course(self.course_id)
        if course is None:
            raise ItemNotFoundError(self.course_id)
        if permission_blacked_out(course, {self.name}, permission):
            return False

        return self.permissions.filter(name=permission).exists()

    @staticmethod
    def user_has_role_for_course(user, course_id, role_names):
        """
        Returns True if the user has one of the given roles for the given course
        """
        return Role.objects.filter(course_id=course_id, name__in=role_names, users=user).exists()


class Permission(models.Model):
    """
    Permissions for django_comment_client

    .. no_pii:
    """
    name = models.CharField(max_length=30, null=False, blank=False, primary_key=True)
    roles = models.ManyToManyField(Role, related_name="permissions")

    class Meta:
        # use existing table that was originally created from lms.djangoapps.discussion.django_comment_client app
        db_table = 'django_comment_client_permission'

    def __str__(self):
        return self.name


def permission_blacked_out(course, role_names, permission_name):
    """
    Returns true if a user in course with the given roles would have permission_name blacked out.

    This will return true if it is a permission that the user might have normally had for the course, but does not have
    right this moment because we are in a discussion blackout period (as defined by the settings on the course block).
    Namely, they can still view, but they can't edit, update, or create anything. This only applies to students, as
    moderators of any kind still have posting privileges during discussion blackouts.
    """
    return (
        not course.forum_posts_allowed and
        role_names == {FORUM_ROLE_STUDENT} and
        any(permission_name.startswith(prefix) for prefix in ['edit', 'update', 'create'])
    )


def all_permissions_for_user_in_course(user, course_id):
    """
    Returns all the permissions the user has in the given course.
    """
    if not user.is_authenticated:
        return {}

    course = modulestore().get_course(course_id)
    if course is None:
        raise ItemNotFoundError(course_id)

    roles = Role.objects.filter(users=user, course_id=course_id)
    role_names = {role.name for role in roles}

    permission_names = set()
    for role in roles:
        # Intentional n+1 query pattern to get permissions for each role because
        # Aurora's query optimizer can't handle the join proplerly on 30M+ row
        # tables (EDUCATOR-3374). Fortunately, there are very few forum roles.
        for permission in role.permissions.all():
            if not permission_blacked_out(course, role_names, permission.name):
                permission_names.add(permission.name)

    # Prevent a circular import
    from openedx.core.djangoapps.django_comment_common.utils import GLOBAL_STAFF_ROLE_PERMISSIONS

    if GlobalStaff().has_user(user):
        for permission in GLOBAL_STAFF_ROLE_PERMISSIONS:
            permission_names.add(permission)

    return permission_names


def has_permission(user, permission, course_id=None):
    """
    This function resolves all discussion-related permissions for the given
    user and course, caches them for the duration of the request, and verifies
    whether the requested permission is present.

    Args:
        user (User): Django user whose permissions are being checked.
        permission (str): Discussion permission identifier
            (e.g., "create_comment", "create_thread").
        course_id (CourseKey): Course context in which to evaluate
            the permission

    Returns:
        bool: True if the user has the specified permission in the given
        course context; False otherwise.
    """
    assert isinstance(course_id, (type(None), CourseKey))
    request_cache_dict = DEFAULT_REQUEST_CACHE.data
    cache_key = "django_comment_client.permissions.has_permission.all_permissions.{}.{}".format(
        user.id, course_id
    )
    if cache_key in request_cache_dict:
        all_permissions = request_cache_dict[cache_key]
    else:
        all_permissions = all_permissions_for_user_in_course(user, course_id)
        request_cache_dict[cache_key] = all_permissions

    return permission in all_permissions


class ForumsConfig(ConfigurationModel):
    """
    Config for the connection to the cs_comments_service forums backend.

    .. no_pii:
    """

    connection_timeout = models.FloatField(
        default=5.0,
        help_text="Seconds to wait when trying to connect to the comment service.",
    )

    class Meta(ConfigurationModel.Meta):
        # use existing table that was originally created from django_comment_common app
        db_table = 'django_comment_common_forumsconfig'

    @property
    def api_key(self):
        """The API key used to authenticate to the comments service."""
        return getattr(settings, "COMMENTS_SERVICE_KEY", None)

    def __str__(self):
        """
        Simple representation so the admin screen looks less ugly.
        """
        return f"ForumsConfig: timeout={self.connection_timeout}"


class CourseDiscussionSettings(models.Model):
    """
    Settings for course discussions

    .. no_pii:
    """
    course_id = CourseKeyField(
        unique=True,
        max_length=255,
        db_index=True,
        help_text="Which course are these settings associated with?",
    )
    discussions_id_map = JSONField(
        null=True,
        blank=True,
        help_text="Key/value store mapping discussion IDs to discussion XBlock usage keys.",
    )
    always_divide_inline_discussions = models.BooleanField(default=False)
    reported_content_email_notifications = models.BooleanField(default=False)
    _divided_discussions = models.TextField(db_column='divided_discussions', null=True, blank=True)  # JSON list

    COHORT = 'cohort'
    ENROLLMENT_TRACK = 'enrollment_track'
    NONE = 'none'
    ASSIGNMENT_TYPE_CHOICES = ((NONE, 'None'), (COHORT, 'Cohort'), (ENROLLMENT_TRACK, 'Enrollment Track'))
    division_scheme = models.CharField(max_length=20, choices=ASSIGNMENT_TYPE_CHOICES, default=NONE)

    class Meta:
        # use existing table that was originally created from django_comment_common app
        db_table = 'django_comment_common_coursediscussionsettings'

    @property
    def divided_discussions(self):
        """
        Jsonify the divided_discussions
        """
        return json.loads(self._divided_discussions)

    @divided_discussions.setter
    def divided_discussions(self, value):
        """
        Un-Jsonify the divided_discussions
        """
        self._divided_discussions = json.dumps(value)

    @request_cached()
    @classmethod
    def get(cls, course_key):
        """
        Get and/or create settings
        """
        try:
            course_discussion_settings = cls.objects.get(course_id=course_key)
        except cls.DoesNotExist:
            from openedx.core.djangoapps.course_groups.cohorts import get_legacy_discussion_settings
            legacy_discussion_settings = get_legacy_discussion_settings(course_key)
            course_discussion_settings, _ = cls.objects.get_or_create(
                course_id=course_key,
                defaults={
                    'always_divide_inline_discussions': legacy_discussion_settings['always_cohort_inline_discussions'],
                    'divided_discussions': legacy_discussion_settings['cohorted_discussions'],
                    'division_scheme': cls.COHORT if legacy_discussion_settings['is_cohorted'] else cls.NONE
                },
            )
        return course_discussion_settings

    def update(self, validated_data: dict):
        """
        Set discussion settings for a course

        Returns:
            A CourseDiscussionSettings object
        """
        fields = {
            'division_scheme': (str,)[0],
            'always_divide_inline_discussions': bool,
            'divided_discussions': list,
            'reported_content_email_notifications': bool,
        }
        for field, field_type in fields.items():
            if field in validated_data:
                if not isinstance(validated_data[field], field_type):
                    raise ValueError(f"Incorrect field type for `{field}`. Type must be `{field_type.__name__}`")
                setattr(self, field, validated_data[field])
        self.save()
        return self


class DiscussionsIdMapping(models.Model):
    """
    This model is a performance optimization, updated on course publish.

    .. no_pii:
    """
    course_id = CourseKeyField(db_index=True, primary_key=True, max_length=255)
    mapping = JSONField(
        help_text="Key/value store mapping discussion IDs to discussion XBlock usage keys.",
    )

    class Meta:
        # use existing table that was originally created from django_comment_common app
        db_table = 'django_comment_common_discussionsidmapping'

    @classmethod
    def update_mapping(cls, course_key, discussions_id_map):
        """Update the mapping of discussions IDs to XBlock usage key strings."""
        mapping_entry, created = cls.objects.get_or_create(
            course_id=course_key,
            defaults={
                'mapping': discussions_id_map,
            },
        )
        if not created:
            mapping_entry.mapping = discussions_id_map
            mapping_entry.save()
