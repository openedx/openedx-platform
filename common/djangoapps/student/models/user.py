"""
Models for User Information (students, staff, etc)

Migration Notes

If you make changes to this model, be sure to create an appropriate migration
file and check it in at the same time as your model changes. To do that,

1. Go to the edx-platform dir
2. ./manage.py lms schemamigration student --auto description_of_your_change
3. Add the migration file created in edx-platform/common/djangoapps/student/migrations/
"""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta
from functools import total_ordering
from importlib import import_module
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import crum
from config_models.models import ConfigurationModel
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import FileExtensionValidator, RegexValidator
from django.db import IntegrityError, models
from django.db.models import Q
from django.db.models.signals import post_save, pre_save, post_delete
from django.db.utils import ProgrammingError
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _
from django.utils.translation import gettext_noop
from django_countries.fields import CountryField
from edx_django_utils import monitoring
from edx_django_utils.cache import RequestCache
from eventtracking import tracker
from model_utils.models import TimeStampedModel
from opaque_keys.edx.django.models import CourseKeyField, LearningContextKeyField
from pytz import UTC, timezone
from openedx.core.lib import user_util

import openedx.core.djangoapps.django_comment_common.comment_client as cc
from common.djangoapps.util.model_utils import emit_field_changed_events, get_changed_fields_dict
from lms.djangoapps.courseware.toggles import streak_celebration_is_active
from openedx.core.djangoapps.signals.signals import USER_ACCOUNT_ACTIVATED
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.xmodule_django.models import NoneToEmptyManager
from openedx.core.djangolib.model_mixins import DeletableByUserValue
from openedx.core.toggles import ENTRANCE_EXAMS

from .course_enrollment import (
    ALLOWEDTOENROLL_TO_ENROLLED,
    CourseEnrollment,
    CourseEnrollmentAllowed,
    CourseOverview,
    ManualEnrollmentAudit,
    segment
)

User = get_user_model()
log = logging.getLogger(__name__)
AUDIT_LOG = logging.getLogger("audit")
SessionStore = import_module(settings.SESSION_ENGINE).SessionStore  # pylint: disable=invalid-name

IS_MARKETABLE = 'is_marketable'

USER_LOGGED_IN_EVENT_NAME = 'edx.user.login'
USER_LOGGED_OUT_EVENT_NAME = 'edx.user.logout'
USER_STREAK_UPDATED_EVENT_NAME = "edx.user.celebration.streak_updated"


class AnonymousUserId(models.Model):
    """
    This table contains user, course_Id and anonymous_user_id

    Purpose of this table is to provide user by anonymous_user_id.

    We generate anonymous_user_id using md5 algorithm,
    and use result in hex form, so its length is equal to 32 bytes.

    .. no_pii: We store anonymous_user_ids here, but do not consider them PII under OEP-30.
    """

    objects = NoneToEmptyManager()

    user = models.ForeignKey(User, db_index=True, on_delete=models.CASCADE)
    anonymous_user_id = models.CharField(unique=True, max_length=32)
    course_id = LearningContextKeyField(db_index=True, blank=True)


def anonymous_id_for_user(user, course_id):
    """
    Inputs:
        user: User model
        course_id: string or None

    Return a unique id for a (user, course_id) pair, suitable for inserting
    into e.g. personalized survey links.

    If user is an `AnonymousUser`, returns `None`
    else If this user/course_id pair already has an anonymous id in AnonymousUserId object, return that
    else: create new anonymous_id, save it in AnonymousUserId, and return anonymous id
    """

    # This part is for ability to get xblock instance in xblock_noauth handlers, where user is unauthenticated.
    assert user

    if user.is_anonymous:
        return None

    # ARCHBOM-1674: Get a sense of what fraction of anonymous_user_id calls are
    # cached, stored in the DB, or retrieved from the DB. This will help inform
    # us on decisions about whether we can
    # pregenerate IDs, use random instead of deterministic IDs, etc.
    monitoring.increment('temp_anon_uid_v2.requested')

    cached_id = getattr(user, '_anonymous_id', {}).get(course_id)
    if cached_id is not None:
        monitoring.increment('temp_anon_uid_v2.returned_from_cache')
        return cached_id
    # Check if an anonymous id already exists for this user and
    # course_id combination. Prefer the one with the highest record ID
    # (see below.)
    anonymous_user_ids = AnonymousUserId.objects.filter(user=user).filter(course_id=course_id).order_by('-id')
    if anonymous_user_ids:
        # If there are multiple anonymous_user_ids per user, course_id pair
        # select the row which was created most recently.
        # There might be more than one if the Django SECRET_KEY had
        # previously been rotated at a time before this function was
        # changed to always save the generated IDs to the DB. In that
        # case, just pick the one with the highest record ID, which is
        # probably the most recently created one.
        anonymous_user_id = anonymous_user_ids[0].anonymous_user_id
        monitoring.increment('temp_anon_uid_v2.fetched_existing')
    else:
        # Uses SECRET_KEY as a cryptographic pepper. This
        # deterministic ID generation means that concurrent identical
        # calls to this function return the same value -- no need for
        # locking. (There may be a low level of integrity errors on
        # creation as a result of concurrent duplicate row inserts.)
        #
        # Consequences for this function of SECRET_KEY exposure: Data
        # researchers and other third parties receiving these
        # anonymous user IDs would be able to identify users across
        # courses, and predict the anonymous user IDs of all users
        # (but not necessarily identify their accounts.)
        #
        # Rotation process of SECRET_KEY with respect to this
        # function: Rotate at will, since the hashes are stored and
        # will not change.
        # include the secret key as a salt, and to make the ids unique across different LMS installs.
        hasher = hashlib.shake_128()
        hasher.update(settings.SECRET_KEY.encode('utf8'))
        hasher.update(str(user.id).encode('utf8'))
        if course_id:
            hasher.update(str(course_id).encode('utf-8'))
        anonymous_user_id = hasher.hexdigest(16)

        try:
            AnonymousUserId.objects.create(
                user=user,
                course_id=course_id,
                anonymous_user_id=anonymous_user_id,
            )
            monitoring.increment('temp_anon_uid_v2.stored')
        except IntegrityError:
            # Another thread has already created this entry, so
            # continue
            monitoring.increment('temp_anon_uid_v2.store_db_error')

    # cache the anonymous_id in the user object
    if not hasattr(user, '_anonymous_id'):
        user._anonymous_id = {}  # pylint: disable=protected-access
    user._anonymous_id[course_id] = anonymous_user_id  # pylint: disable=protected-access

    return anonymous_user_id


def user_by_anonymous_id(uid):
    """
    Return user by anonymous_user_id using AnonymousUserId lookup table.

    Do not raise `django.ObjectDoesNotExist` exception,
    if there is no user for anonymous_student_id,
    because this function will be used inside xmodule w/o django access.
    """

    if uid is None:
        return None

    request_cache = RequestCache('user_by_anonymous_id')
    cache_response = request_cache.get_cached_response(uid)
    if cache_response.is_found:
        return cache_response.value

    try:
        user = User.objects.get(anonymoususerid__anonymous_user_id=uid)
        request_cache.set(uid, user)
        return user
    except ObjectDoesNotExist:
        request_cache.set(uid, None)
        return None


def is_username_retired(username):
    """
    Checks to see if the given username has been previously retired
    """
    locally_hashed_usernames = user_util.get_all_retired_usernames(
        username,
        settings.RETIRED_USER_SALTS,
        settings.RETIRED_USERNAME_FMT
    )

    # TODO: Revert to this after username capitalization issues detailed in
    # PLAT-2276, PLAT-2277, PLAT-2278 are sorted out:
    # return User.objects.filter(username__in=list(locally_hashed_usernames)).exists()

    # Avoid circular import issues
    from openedx.core.djangoapps.user_api.models import UserRetirementStatus

    # Sandbox clean builds attempt to create users during migrations, before the database
    # is stable so UserRetirementStatus may not exist yet. This workaround can also go
    # when we are done with the username updates.
    try:
        return User.objects.filter(username__in=list(locally_hashed_usernames)).exists() or \
            UserRetirementStatus.objects.filter(original_username=username).exists()
    except ProgrammingError as exc:
        # Check the error message to make sure it's what we expect
        if "user_api_userretirementstatus" in str(exc):
            return User.objects.filter(username__in=list(locally_hashed_usernames)).exists()
        raise


def username_exists_or_retired(username):
    """
    Check a username for existence -or- retirement against the User model.
    """
    return User.objects.filter(username=username).exists() or is_username_retired(username)


def is_email_retired(email):
    """
    Checks to see if the given email has been previously retired
    """
    locally_hashed_emails = user_util.get_all_retired_emails(
        email,
        settings.RETIRED_USER_SALTS,
        settings.RETIRED_EMAIL_FMT
    )

    return User.objects.filter(email__in=list(locally_hashed_emails)).exists()


def email_exists_or_retired(email):
    """
    Check an email against the User model for existence.
    """
    return (
        User.objects.filter(email=email).exists() or
        is_email_retired(email) or
        AccountRecovery.objects.filter(secondary_email=email).exists()
    )


def get_retired_username_by_username(username):
    """
    If a UserRetirementStatus object with an original_username matching the given username exists,
    returns that UserRetirementStatus.retired_username value.  Otherwise, returns a "retired username"
    hashed using the newest configured salt.
    """
    UserRetirementStatus = apps.get_model('user_api', 'UserRetirementStatus')
    try:
        status = UserRetirementStatus.objects.filter(original_username=username).order_by('-modified').first()
        if status:
            return status.retired_username
    except UserRetirementStatus.DoesNotExist:
        pass
    return user_util.get_retired_username(username, settings.RETIRED_USER_SALTS, settings.RETIRED_USERNAME_FMT)


def get_retired_email_by_email(email):
    """
    If a UserRetirementStatus object with an original_email matching the given email exists,
    returns that UserRetirementStatus.retired_email value.  Otherwise, returns a "retired email"
    hashed using the newest configured salt.
    """
    UserRetirementStatus = apps.get_model('user_api', 'UserRetirementStatus')
    try:
        status = UserRetirementStatus.objects.filter(original_email=email).order_by('-modified').first()
        if status:
            return status.retired_email
    except UserRetirementStatus.DoesNotExist:
        pass
    return user_util.get_retired_email(email, settings.RETIRED_USER_SALTS, settings.RETIRED_EMAIL_FMT)


def get_all_retired_usernames_by_username(username):
    """
    Returns a generator of "retired usernames", one hashed with each
    configured salt. Used for finding out if the given username has
    ever been used and retired.
    """
    return user_util.get_all_retired_usernames(username, settings.RETIRED_USER_SALTS, settings.RETIRED_USERNAME_FMT)


def _get_all_retired_emails_by_email(email):
    """
    Returns a generator of "retired emails", one hashed with each
    configured salt. Used for finding out if the given email has
    ever been used and retired.
    """
    return user_util.get_all_retired_emails(email, settings.RETIRED_USER_SALTS, settings.RETIRED_EMAIL_FMT)


def get_potentially_retired_user_by_username(username):
    """
    Attempt to return a User object based on the username, or if it
    does not exist, then any hashed username salted with the historical
    salts.
    """
    locally_hashed_usernames = list(get_all_retired_usernames_by_username(username))
    locally_hashed_usernames.append(username)
    potential_users = User.objects.filter(username__in=locally_hashed_usernames)

    # Have to disambiguate between several Users here as we could have retirees with
    # the same username, but for case.
    # If there's only 1 we're done, this should be the common case
    if len(potential_users) == 1:
        return potential_users[0]

    # No user found, throw the usual error
    if not potential_users:
        raise User.DoesNotExist()

    # For a brief period, users were able to retire accounts and make another account with
    # the same differently-cased username, like "testuser" and "TestUser".
    # If there are two users found, return the one that's the *actual* case-matching username,
    # whether retired or not.
    if len(potential_users) == 2:
        # Figure out which user has been retired.
        if potential_users[0].username.startswith(settings.RETIRED_USERNAME_PREFIX):
            retired = potential_users[0]
            active = potential_users[1]
        else:
            retired = potential_users[1]
            active = potential_users[0]

        # If the active (non-retired) user's username doesn't *exactly* match (including case),
        # then the retired account must be the one that exactly matches.
        return active if active.username == username else retired

    # We should have, at most, a retired username and an active one with a username
    # differing only by case. If there are more we need to disambiguate them by hand.
    raise Exception(f'Expected 1 or 2 Users, received {str(potential_users)}')


def get_potentially_retired_user_by_username_and_hash(username, hashed_username):
    """
    To assist in the retirement process this method will:
    - Confirm that any locally hashed username matches the passed in one
      (in case of salt mismatches with the upstream script).
    - Attempt to return a User object based on the username, or if it
      does not exist, the any hashed username salted with the historical
      salts.
    """
    locally_hashed_usernames = list(get_all_retired_usernames_by_username(username))

    if hashed_username not in locally_hashed_usernames:
        raise Exception('Mismatched hashed_username, bad salt?')

    locally_hashed_usernames.append(username)
    return User.objects.get(username__in=locally_hashed_usernames)


class UserStanding(models.Model):
    """
    This table contains a student's account's status.
    Currently, we're only disabling accounts; in the future we can imagine
    taking away more specific privileges, like forums access, or adding
    more specific karma levels or probationary stages.

    .. no_pii:
    """
    ACCOUNT_DISABLED = "disabled"
    ACCOUNT_ENABLED = "enabled"
    USER_STANDING_CHOICES = (
        (ACCOUNT_DISABLED, "Account Disabled"),
        (ACCOUNT_ENABLED, "Account Enabled"),
    )

    user = models.OneToOneField(User, db_index=True, related_name='standing', on_delete=models.CASCADE)
    account_status = models.CharField(
        blank=True, max_length=31, choices=USER_STANDING_CHOICES
    )
    changed_by = models.ForeignKey(User, blank=True, on_delete=models.CASCADE)
    standing_last_changed_at = models.DateTimeField(auto_now=True)


class UserProfile(models.Model):
    """This is where we store all the user demographic fields. We have a
    separate table for this rather than extending the built-in Django auth_user.

    Notes:
        * Some fields are legacy ones from the first run of 6.002, from which
          we imported many users.
        * Fields like name and address are intentionally open ended, to account
          for international variations. An unfortunate side-effect is that we
          cannot efficiently sort on last names for instance.

    Replication:
        * Only the Portal servers should ever modify this information.
        * All fields are replicated into relevant Course databases

    Some of the fields are legacy ones that were captured during the initial
    MITx fall prototype.

    .. pii: Contains many PII fields. Retired in AccountRetirementView.
    .. pii_types: name, location, birth_date, gender, biography, phone_number
    .. pii_retirement: local_api
    """
    # cache key format e.g user.<user_id>.profile.country = 'SG'
    PROFILE_COUNTRY_CACHE_KEY = "user.{user_id}.profile.country"

    class Meta:
        db_table = "auth_userprofile"
        permissions = (("can_deactivate_users", "Can deactivate, but NOT delete users"),)

    # CRITICAL TODO/SECURITY
    # Sanitize all fields.
    # This is not visible to other users, but could introduce holes later
    user = models.OneToOneField(User, unique=True, db_index=True, related_name='profile', on_delete=models.CASCADE)
    name = models.CharField(blank=True, max_length=255, db_index=True)

    # How meta field works: meta will only store those fields which are available in extended_profile configuration,
    # so in order to store a field in meta, it must be available in extended_profile configuration.
    meta = models.TextField(blank=True)  # JSON dictionary for future expansion
    courseware = models.CharField(blank=True, max_length=255, default='course.xml')

    # Language is deprecated and no longer used. Old rows exist that have
    # user-entered free form text values (ex. "English"), some of which have
    # non-ASCII values. You probably want UserPreference version of this, which
    # stores the user's preferred language code. See openedx/core/djangoapps/lang_pref
    # for more information.
    language = models.CharField(blank=True, max_length=255, db_index=True)

    # Location is no longer used, but is held here for backwards compatibility
    # for users imported from our first class.
    location = models.CharField(blank=True, max_length=255, db_index=True)

    # Optional demographic data we started capturing from Fall 2012
    this_year = datetime.now(ZoneInfo("UTC")).year
    VALID_YEARS = list(range(this_year, this_year - 120, -1))
    year_of_birth = models.IntegerField(blank=True, null=True, db_index=True)
    GENDER_CHOICES = (
        ('m', gettext_noop('Male')),
        ('f', gettext_noop('Female')),
        # Translators: 'Other' refers to the student's gender
        ('o', gettext_noop('Other/Prefer Not to Say'))
    )
    gender = models.CharField(
        blank=True, null=True, max_length=6, db_index=True, choices=GENDER_CHOICES
    )

    # [03/21/2013] removed these, but leaving comment since there'll still be
    # p_se and p_oth in the existing data in db.
    # ('p_se', 'Doctorate in science or engineering'),
    # ('p_oth', 'Doctorate in another field'),
    LEVEL_OF_EDUCATION_CHOICES = (
        ('p', gettext_noop('Doctorate')),
        ('m', gettext_noop("Master's or professional degree")),
        ('b', gettext_noop("Bachelor's degree")),
        ('a', gettext_noop("Associate degree")),
        ('hs', gettext_noop("Secondary/high school")),
        ('jhs', gettext_noop("Junior secondary/junior high/middle school")),
        ('el', gettext_noop("Elementary/primary school")),
        # Translators: 'None' refers to the student's level of education
        ('none', gettext_noop("No formal education")),
        # Translators: 'Other' refers to the student's level of education
        ('other', gettext_noop("Other education"))
    )
    level_of_education = models.CharField(
        blank=True, null=True, max_length=6, db_index=True,
        choices=LEVEL_OF_EDUCATION_CHOICES
    )
    mailing_address = models.TextField(blank=True, null=True)
    city = models.TextField(blank=True, null=True)
    country = CountryField(blank=True, null=True)
    COUNTRY_WITH_STATES = 'US'
    STATE_CHOICES = (
        ('AL', 'Alabama'),
        ('AK', 'Alaska'),
        ('AZ', 'Arizona'),
        ('AR', 'Arkansas'),
        ('AA', 'Armed Forces Americas'),
        ('AE', 'Armed Forces Europe'),
        ('AP', 'Armed Forces Pacific'),
        ('CA', 'California'),
        ('CO', 'Colorado'),
        ('CT', 'Connecticut'),
        ('DE', 'Delaware'),
        ('DC', 'District Of Columbia'),
        ('FL', 'Florida'),
        ('GA', 'Georgia'),
        ('HI', 'Hawaii'),
        ('ID', 'Idaho'),
        ('IL', 'Illinois'),
        ('IN', 'Indiana'),
        ('IA', 'Iowa'),
        ('KS', 'Kansas'),
        ('KY', 'Kentucky'),
        ('LA', 'Louisiana'),
        ('ME', 'Maine'),
        ('MD', 'Maryland'),
        ('MA', 'Massachusetts'),
        ('MI', 'Michigan'),
        ('MN', 'Minnesota'),
        ('MS', 'Mississippi'),
        ('MO', 'Missouri'),
        ('MT', 'Montana'),
        ('NE', 'Nebraska'),
        ('NV', 'Nevada'),
        ('NH', 'New Hampshire'),
        ('NJ', 'New Jersey'),
        ('NM', 'New Mexico'),
        ('NY', 'New York'),
        ('NC', 'North Carolina'),
        ('ND', 'North Dakota'),
        ('OH', 'Ohio'),
        ('OK', 'Oklahoma'),
        ('OR', 'Oregon'),
        ('PA', 'Pennsylvania'),
        ('RI', 'Rhode Island'),
        ('SC', 'South Carolina'),
        ('SD', 'South Dakota'),
        ('TN', 'Tennessee'),
        ('TX', 'Texas'),
        ('UT', 'Utah'),
        ('VT', 'Vermont'),
        ('VA', 'Virginia'),
        ('WA', 'Washington'),
        ('WV', 'West Virginia'),
        ('WI', 'Wisconsin'),
        ('WY', 'Wyoming'),
    )
    state = models.CharField(blank=True, null=True, max_length=2, choices=STATE_CHOICES)
    goals = models.TextField(blank=True, null=True)
    bio = models.CharField(blank=True, null=True, max_length=3000, db_index=False)
    profile_image_uploaded_at = models.DateTimeField(null=True, blank=True)
    phone_regex = RegexValidator(
        regex=r'^\+?1?\d*$',
        message="Phone number must start with '+' (optional) followed by digits (0-9) only.",
    )
    phone_number = models.CharField(validators=[phone_regex], blank=True, null=True, max_length=50)

    @property
    def has_profile_image(self):
        """
        Convenience method that returns a boolean indicating whether or not
        this user has uploaded a profile image.
        """
        return self.profile_image_uploaded_at is not None

    @property
    def age(self):
        """ Convenience method that returns the age given a year_of_birth. """
        year_of_birth = self.year_of_birth
        year = datetime.now(ZoneInfo("UTC")).year
        if year_of_birth is not None:
            return self._calculate_age(year, year_of_birth)

    @property
    def level_of_education_display(self):
        """ Convenience method that returns the human readable level of education. """
        if self.level_of_education:
            return self.__enumerable_to_display(self.LEVEL_OF_EDUCATION_CHOICES, self.level_of_education)

    @property
    def gender_display(self):
        """ Convenience method that returns the human readable gender. """
        if self.gender:
            return self.__enumerable_to_display(self.GENDER_CHOICES, self.gender)

    def get_meta(self):  # pylint: disable=missing-function-docstring
        js_str = self.meta
        if not js_str:
            js_str = {}
        else:
            js_str = json.loads(self.meta)

        return js_str

    def set_meta(self, meta_json):
        self.meta = json.dumps(meta_json)

    def set_login_session(self, session_id=None):
        """
        Sets the current session id for the logged-in user.
        If session_id doesn't match the existing session,
        deletes the old session object.
        """
        meta = self.get_meta()
        old_login = meta.get('session_id', None)
        if old_login:
            SessionStore(session_key=old_login).delete()
        meta['session_id'] = session_id
        self.set_meta(meta)
        self.save()

    def requires_parental_consent(self, year=None, age_limit=None, default_requires_consent=True):
        """Returns true if this user requires parental consent.

        Args:
            year (int): The year for which consent needs to be tested (defaults to now).
            age_limit (int): The age limit at which parental consent is no longer required.
                This defaults to the value of the setting 'PARENTAL_CONTROL_AGE_LIMIT'.
            default_requires_consent (bool): True if users require parental consent if they
                have no specified year of birth (default is True).

        Returns:
             True if the user requires parental consent.
        """
        if age_limit is None:
            age_limit = getattr(settings, 'PARENTAL_CONSENT_AGE_LIMIT', None)
            if age_limit is None:
                return False

        # Return True if either:
        # a) The user has a year of birth specified and that year is fewer years in the past than the limit.
        # b) The user has no year of birth specified and the default is to require consent.
        #
        # Note: we have to be conservative using the user's year of birth as their birth date could be
        # December 31st. This means that if the number of years since their birth year is exactly equal
        # to the age limit then we have to assume that they might still not be old enough.
        year_of_birth = self.year_of_birth
        if year_of_birth is None:
            return default_requires_consent

        if year is None:
            age = self.age
        else:
            age = self._calculate_age(year, year_of_birth)

        return age < age_limit

    def __enumerable_to_display(self, enumerables, enum_value):
        """ Get the human readable value from an enumerable list of key-value pairs. """
        return dict(enumerables)[enum_value]

    def _calculate_age(self, year, year_of_birth):
        """Calculate the youngest age for a user with a given year of birth.

        :param year: year
        :param year_of_birth: year of birth
        :return: youngest age a user could be for the given year
        """
        # There are legal implications regarding how we can contact users and what information we can make public
        # based on their age, so we must take the most conservative estimate.
        return year - year_of_birth - 1

    @classmethod
    def country_cache_key_name(cls, user_id):
        """Return cache key name to be used to cache current country.
        Args:
            user_id(int): Id of user.

        Returns:
            Unicode cache key
        """
        return cls.PROFILE_COUNTRY_CACHE_KEY.format(user_id=user_id)


@receiver(models.signals.post_save, sender=UserProfile)
def invalidate_user_profile_country_cache(sender, instance, **kwargs):  # pylint:   disable=unused-argument
    """Invalidate the cache of country in UserProfile model. """

    changed_fields = getattr(instance, '_changed_fields', {})

    if 'country' in changed_fields:
        cache_key = UserProfile.country_cache_key_name(instance.user_id)
        cache.delete(cache_key)
        log.info("Country changed in UserProfile for %s, cache deleted", instance.user_id)


@receiver(pre_save, sender=UserProfile)
def user_profile_pre_save_callback(sender, **kwargs):
    """
    Ensure consistency of a user profile before saving it.
    """
    user_profile = kwargs['instance']

    # Cache "old" field values on the model instance so that they can be
    # retrieved in the post_save callback when we emit an event with new and
    # old field values.
    user_profile._changed_fields = get_changed_fields_dict(user_profile, sender)  # lint-amnesty, pylint: disable=protected-access


@receiver(post_save, sender=UserProfile)
def user_profile_post_save_callback(sender, **kwargs):
    """
    Emit analytics events after saving the UserProfile.
    """
    user_profile = kwargs['instance']
    emit_field_changed_events(
        user_profile,
        user_profile.user,
        sender._meta.db_table,
        excluded_fields=['meta']
    )


@receiver(pre_save, sender=User)
def user_pre_save_callback(sender, **kwargs):
    """
    Capture old fields on the user instance before save and cache them as a
    private field on the current model for use in the post_save callback.
    """
    user = kwargs['instance']
    user._changed_fields = get_changed_fields_dict(user, sender)  # lint-amnesty, pylint: disable=protected-access


@receiver(post_save, sender=User)
def user_post_save_callback(sender, **kwargs):
    """
    When a user is modified and either its `is_active` state or email address
    is changed, and the user is, in fact, active, then check to see if there
    are any courses that it needs to be automatically enrolled in and enroll them if needed.

    Additionally, emit analytics events after saving the User.
    """
    user = kwargs['instance']

    changed_fields = user._changed_fields  # lint-amnesty, pylint: disable=protected-access

    if 'is_active' in changed_fields or 'email' in changed_fields:
        if user.is_active:
            ceas = CourseEnrollmentAllowed.for_user(user).filter(auto_enroll=True)

            for cea in ceas:
                # skip enrolling already enrolled users
                if CourseEnrollment.is_enrolled(user, cea.course_id):
                    # Link the CEA to the user if the CEA isn't already linked to the user
                    # (e.g. the user was invited to a course but hadn't activated the account yet)
                    # This is to prevent students from changing e-mails and
                    # enrolling many accounts through the same e-mail.
                    if not cea.user:
                        cea.user = user
                        cea.save()
                    continue

                # Skip auto enrollment of user if enrollment is not open for the course
                # We are checking this here instead of passing check_access=True to CourseEnrollment.enroll()
                # as we want to skip course full check.
                if CourseEnrollment.is_enrollment_closed(user, CourseOverview.get_from_id(cea.course_id)):
                    log.info(f'Skipping auto enrollment of user as enrollment for course {cea.course_id} has ended')
                    continue

                enrollment = CourseEnrollment.enroll(user, cea.course_id)

                manual_enrollment_audit = ManualEnrollmentAudit.get_manual_enrollment_by_email(user.email)
                if manual_enrollment_audit is not None:
                    # get the enrolled by user and reason from the ManualEnrollmentAudit table.
                    # then create a new ManualEnrollmentAudit table entry for the same email
                    # different transition state.
                    ManualEnrollmentAudit.create_manual_enrollment_audit(
                        manual_enrollment_audit.enrolled_by,
                        user.email,
                        ALLOWEDTOENROLL_TO_ENROLLED,
                        manual_enrollment_audit.reason,
                        enrollment
                    )

    # Ensure the user has a profile when run via management command
    _called_by_management_command = getattr(user, '_called_by_management_command', None)
    if _called_by_management_command:
        try:
            profile = user.profile
        except UserProfile.DoesNotExist:
            profile = UserProfile.objects.create(user=user)
            log.info('Created new profile for user: %s', user)

        # If user is created using management command, ensure that the user's
        # marketable attribute is set (default: False) and an account is created
        # on segment. By created an account on segment, it is ensured that data
        # will be sent to relevant places like Braze.
        if settings.MARKETING_EMAILS_OPT_IN:
            UserAttribute.set_user_attribute(user, IS_MARKETABLE, 'false')

            traits = {
                'email': user.email,
                'username': user.username,
                'name': profile.name,
                'age': profile.age or -1,
                'yearOfBirth': profile.year_of_birth or datetime.now(ZoneInfo("UTC")).year,
                'education': profile.level_of_education_display,
                'address': profile.mailing_address,
                'gender': profile.gender_display,
                'country': str(profile.country),
                'is_marketable': False
            }
            # .. pii: Many pieces of PII are sent to Segment here. Retired directly through Segment API call in Tubular.
            # .. pii_types: email_address, username
            # .. pii_retirement: third_party
            segment.identify(user.id, traits)

    # Because `emit_field_changed_events` removes the record of the fields that
    # were changed, wait to do that until after we've checked them as part of
    # the condition on whether we want to check for automatic enrollments.
    emit_field_changed_events(
        user,
        user,
        sender._meta.db_table,
        excluded_fields=['last_login', 'first_name', 'last_name'],
        hidden_fields=['password']
    )


class UserSignupSource(models.Model):
    """
    This table contains information about users registering
    via Micro-Sites

    .. no_pii:
    """
    user = models.ForeignKey(User, db_index=True, on_delete=models.CASCADE)
    site = models.CharField(max_length=255, db_index=True)


def unique_id_for_user(user):
    """
    Return a unique id for a user, suitable for inserting into
    e.g. personalized survey links.
    """
    # Setting course_id to '' makes it not affect the generated hash,
    # and thus produce the old per-student anonymous id
    return anonymous_id_for_user(user, None)


# TODO: Should be renamed to generic UserGroup, and possibly
# Given an optional field for type of group
class UserTestGroup(models.Model):
    """
    .. no_pii:
    """
    users = models.ManyToManyField(User, db_index=True)
    name = models.CharField(blank=False, max_length=32, db_index=True)
    description = models.TextField(blank=True)


class Registration(models.Model):
    """
    Allows us to wait for e-mail before user is registered. A
    registration profile is created when the user creates an
    account, but that account is inactive. Once the user clicks
    on the activation key, it becomes active.

    .. no_pii:
    """

    class Meta:
        db_table = "auth_registration"

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    activation_key = models.CharField(('activation key'), max_length=32, unique=True, db_index=True)
    activation_timestamp = models.DateTimeField(default=None, null=True, blank=True)

    def register(self, user):
        # MINOR TODO: Switch to crypto-secure key
        self.activation_key = uuid.uuid4().hex
        self.user = user
        self.save()

    def activate(self):  # lint-amnesty, pylint: disable=missing-function-docstring
        self.user.is_active = True
        self.user.save(update_fields=['is_active'])
        self.activation_timestamp = datetime.utcnow()
        self.save()
        USER_ACCOUNT_ACTIVATED.send_robust(self.__class__, user=self.user)
        log.info('User %s (%s) account is successfully activated.', self.user.username, self.user.email)


class PendingNameChange(DeletableByUserValue, models.Model):
    """
    This model keeps track of pending requested changes to a user's name.

    .. pii: Contains new_name, retired in LMSAccountRetirementView
    .. pii_types: name
    .. pii_retirement: local_api
    """
    user = models.OneToOneField(User, unique=True, db_index=True, on_delete=models.CASCADE)
    new_name = models.CharField(blank=True, max_length=255)
    rationale = models.CharField(blank=True, max_length=1024)


class PendingEmailChange(DeletableByUserValue, models.Model):
    """
    This model keeps track of pending requested changes to a user's email address.

    .. pii: Contains new_email, retired in AccountRetirementView
    .. pii_types: email_address
    .. pii_retirement: local_api
    """
    user = models.OneToOneField(User, unique=True, db_index=True, on_delete=models.CASCADE)
    new_email = models.CharField(blank=True, max_length=255, db_index=True)
    activation_key = models.CharField(('activation key'), max_length=32, unique=True, db_index=True)

    def request_change(self, email):
        """Request a change to a user's email.

        Implicitly saves the pending email change record.

        Arguments:
            email (unicode): The proposed new email for the user.

        Returns:
            unicode: The activation code to confirm the change.

        """
        self.new_email = email
        self.activation_key = uuid.uuid4().hex
        self.save()
        return self.activation_key


class PendingSecondaryEmailChange(DeletableByUserValue, models.Model):
    """
    This model keeps track of pending requested changes to a user's secondary email address.

    .. pii: Contains new_secondary_email, not currently retired
    .. pii_types: email_address
    .. pii_retirement: retained
    """
    user = models.OneToOneField(User, unique=True, db_index=True, on_delete=models.CASCADE)
    new_secondary_email = models.CharField(blank=True, max_length=255, db_index=True)
    activation_key = models.CharField(('activation key'), max_length=32, unique=True, db_index=True)


class LoginFailures(models.Model):
    """
    This model will keep track of failed login attempts.

    .. no_pii:
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    failure_count = models.IntegerField(default=0)
    lockout_until = models.DateTimeField(null=True)

    @classmethod
    def _get_record_for_user(cls, user):
        """
        Gets a user's record, and fixes any duplicates that may have arisen due to get_or_create
        race conditions. See https://code.djangoproject.com/ticket/13906 for details.

        Use this method in place of `LoginFailures.objects.get(user=user)`
        """
        records = LoginFailures.objects.filter(user=user).order_by('-lockout_until')
        for extra_record in records[1:]:
            extra_record.delete()
        return records.get()

    @classmethod
    def is_feature_enabled(cls):
        """
        Returns whether the feature flag around this functionality has been set
        """
        return settings.FEATURES['ENABLE_MAX_FAILED_LOGIN_ATTEMPTS']

    @classmethod
    def is_user_locked_out(cls, user):
        """
        Static method to return in a given user has his/her account locked out
        """
        try:
            record = cls._get_record_for_user(user)
            if not record.lockout_until:
                return False

            now = datetime.now(ZoneInfo("UTC"))
            until = record.lockout_until
            is_locked_out = until and now < until

            return is_locked_out
        except ObjectDoesNotExist:
            return False

    @classmethod
    def increment_lockout_counter(cls, user):
        """
        Ticks the failed attempt counter
        """
        record, _ = LoginFailures.objects.get_or_create(user=user)
        record.failure_count = record.failure_count + 1
        max_failures_allowed = settings.MAX_FAILED_LOGIN_ATTEMPTS_ALLOWED

        # did we go over the limit in attempts
        if record.failure_count >= max_failures_allowed:
            # yes, then store when this account is locked out until
            lockout_period_secs = settings.MAX_FAILED_LOGIN_ATTEMPTS_LOCKOUT_PERIOD_SECS
            record.lockout_until = datetime.now(ZoneInfo("UTC")) + timedelta(seconds=lockout_period_secs)

        record.save()

    @classmethod
    def check_user_reset_password_threshold(cls, user):
        """
        Checks if the user is above threshold for reset password message.
        """
        record, _ = LoginFailures.objects.get_or_create(user=user)
        max_failures_allowed = settings.MAX_FAILED_LOGIN_ATTEMPTS_ALLOWED

        return record.failure_count >= max_failures_allowed / 2, record.failure_count

    @classmethod
    def clear_lockout_counter(cls, user):
        """
        Removes the lockout counters (normally called after a successful login)
        """
        try:
            entry = cls._get_record_for_user(user)
            entry.delete()
        except ObjectDoesNotExist:
            pass

    def __str__(self):
        """Str -> Username: count - date."""
        return '{username}: {count} - {date}'.format(
            username=self.user.username,
            count=self.failure_count,
            date=self.lockout_until.isoformat() if self.lockout_until else '-'
        )

    class Meta:
        verbose_name = 'Login Failure'
        verbose_name_plural = 'Login Failures'


@total_ordering
class CourseAccessRole(models.Model):
    """
    Maps users to org, courses, and roles. Used by student.roles.CourseRole and OrgRole.
    To establish a user as having a specific role over all courses in the org, create an entry
    without a course_id.

    .. no_pii:
    """

    objects = NoneToEmptyManager()

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    # blank org is for global group based roles such as course creator (may be deprecated)
    org = models.CharField(max_length=64, db_index=True, blank=True)
    # blank course_id implies org wide role
    course_id = CourseKeyField(db_index=True, blank=True)
    role = models.CharField(max_length=64, db_index=True)

    class Meta:
        unique_together = ('user', 'org', 'course_id', 'role')

    @property
    def _key(self):
        """
        convenience function to make eq overrides easier and clearer. arbitrary decision
        that role is primary, followed by org, course, and then user
        """
        return (self.role, self.org, self.course_id, self.user_id)

    @classmethod
    def access_roles_in_course(cls, course_key):
        """
        Returns all CourseAccessRole for a given course and prefetches user information.
        """
        return cls.objects.filter(
            course_id=course_key,
        ).select_related(
            'user',
            'user__profile'
        )

    def __eq__(self, other):
        """
        Overriding eq b/c the django impl relies on the primary key which requires fetch. sometimes we
        just want to compare roles w/o doing another fetch.
        """
        return type(self) == type(other) and self._key == other._key  # lint-amnesty, pylint: disable=protected-access, unidiomatic-typecheck

    def __hash__(self):
        return hash(self._key)

    def __lt__(self, other):
        """
        Lexigraphic sort
        """
        return self._key < other._key

    def __str__(self):
        return f"[CourseAccessRole] user: {self.user.username}   role: {self.role}   org: {self.org}   course: {self.course_id}"  # lint-amnesty, pylint: disable=line-too-long


class CourseAccessRoleHistory(TimeStampedModel):
    """
    Stores the change history for CourseAccessRole objects.
    """
    ACTION_CHOICES = (
        ('created', 'Created'),
        ('updated', 'Updated'),
        ('deleted', 'Deleted'),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    org = models.CharField(max_length=64, db_index=True, blank=True)
    course_id = CourseKeyField(db_index=True, blank=True)
    role = models.CharField(max_length=64, db_index=True)
    action_type = models.CharField(max_length=10, choices=ACTION_CHOICES, db_index=True)
    changed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='courseaccessrole_history_changer',
    )
    old_values = models.JSONField(null=True, blank=True, help_text="Stores old values of fields for 'updated' actions.")

    class Meta:
        permissions = (("can_revert_course_access_role", "Can revert course access role changes"),
                       ("can_delete_course_access_role_history", "Can delete course access role history"),)

    def __str__(self):
        return (
            f"[CourseAccessRoleHistory] user: {self.user.username} role: {self.role} "
            f"org: {self.org} course: {self.course_id} action: {self.action_type} "
            f"changed_by: {self.changed_by.username if self.changed_by else 'N/A'} at {self.created}"
        )


#### Helper methods for use from python manage.py shell and other classes.

def strip_if_string(value):
    if isinstance(value, str):
        return value.strip()
    return value


def get_user_by_username_or_email(username_or_email):
    """
    Return a User object by looking up a user against username_or_email.

    Raises:
        User.DoesNotExist if no user object can be found, the user was
        retired, or the user is in the process of being retired.

        MultipleObjectsReturned if one user has same email as username of
        second user

        MultipleObjectsReturned if more than one user has same email or
        username
    """
    username_or_email = strip_if_string(username_or_email)
    # there should be one user with either username or email equal to username_or_email
    user = User.objects.get(Q(email=username_or_email) | Q(username=username_or_email))
    if user.username == username_or_email:
        UserRetirementRequest = apps.get_model('user_api', 'UserRetirementRequest')
        if UserRetirementRequest.has_user_requested_retirement(user):
            raise User.DoesNotExist
    return user


def get_user(email):
    user = User.objects.get(email=email)
    u_prof = UserProfile.objects.get(user=user)
    return user, u_prof


def user_info(email):  # lint-amnesty, pylint: disable=missing-function-docstring
    user, u_prof = get_user(email)
    print("User id", user.id)
    print("Username", user.username)
    print("E-mail", user.email)
    print("Name", u_prof.name)
    print("Location", u_prof.location)
    print("Language", u_prof.language)
    return user, u_prof


def change_email(old_email, new_email):
    user = User.objects.get(email=old_email)
    user.email = new_email
    user.save()


def change_name(email, new_name):
    _user, u_prof = get_user(email)
    u_prof.name = new_name
    u_prof.save()


def user_count():
    print("All users", User.objects.all().count())
    print("Active users", User.objects.filter(is_active=True).count())
    return User.objects.all().count()


def active_user_count():
    return User.objects.filter(is_active=True).count()


def create_group(name, description):
    utg = UserTestGroup()
    utg.name = name
    utg.description = description
    utg.save()


def add_user_to_group(user, group):
    utg = UserTestGroup.objects.get(name=group)
    utg.users.add(User.objects.get(username=user))
    utg.save()


def remove_user_from_group(user, group):
    utg = UserTestGroup.objects.get(name=group)
    utg.users.remove(User.objects.get(username=user))
    utg.save()

DEFAULT_GROUPS = {
    'email_future_courses': 'Receive e-mails about future MITx courses',
    'email_helpers': 'Receive e-mails about how to help with MITx',
    'mitx_unenroll': 'Fully unenrolled -- no further communications',
    '6002x_unenroll': 'Took and dropped 6002x'
}


def add_user_to_default_group(user, group):  # lint-amnesty, pylint: disable=missing-function-docstring
    try:
        utg = UserTestGroup.objects.get(name=group)
    except UserTestGroup.DoesNotExist:
        utg = UserTestGroup()
        utg.name = group
        utg.description = DEFAULT_GROUPS[group]
        utg.save()
    utg.users.add(User.objects.get(username=user))
    utg.save()


def create_comments_service_user(user):  # lint-amnesty, pylint: disable=missing-function-docstring
    if not settings.FEATURES['ENABLE_DISCUSSION_SERVICE']:
        # Don't try--it won't work, and it will fill the logs with lots of errors
        return
    try:
        cc_user = cc.User.from_django_user(user)
        cc_user.save()
    except Exception:  # pylint: disable=broad-except
        log = logging.getLogger("edx.discussion")  # pylint: disable=redefined-outer-name
        log.error(
            f"Could not create comments service user with id {user.id}",
            exc_info=True
        )

# Define login and logout handlers here in the models file, instead of the views file,
# so that they are more likely to be loaded when a Studio user brings up the Studio admin
# page to login.  These are currently the only signals available, so we need to continue
# identifying and logging failures separately (in views).


@receiver(user_logged_in)
def log_successful_login(sender, request, user, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
    """Handler to log when logins have occurred successfully."""
    tracker.emit(
        USER_LOGGED_IN_EVENT_NAME,
        {
            'user_id': getattr(user, 'id', None) if user else None,
            'event_type': "login",
        }
    )
    if settings.FEATURES['SQUELCH_PII_IN_LOGS']:
        AUDIT_LOG.info(f"Login success - user.id: {user.id}")
    else:
        AUDIT_LOG.info(f"Login success - {user.username} ({user.email})")


@receiver(user_logged_out)
def log_successful_logout(sender, request, user, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
    """Handler to log when logouts have occurred successfully."""
    if hasattr(request, 'user'):
        tracker.emit(
            USER_LOGGED_OUT_EVENT_NAME,
            {
                'user_id': getattr(user, 'id', None) if user else None,
                'event_type': "logout",
            }
        )
        if settings.FEATURES['SQUELCH_PII_IN_LOGS']:
            AUDIT_LOG.info(f'Logout - user.id: {request.user.id}')  # pylint: disable=logging-format-interpolation
        else:
            AUDIT_LOG.info(f'Logout - {request.user}')  # pylint: disable=logging-format-interpolation
        if request.user.id:
            segment.track(request.user.id, 'edx.bi.user.account.logout')


@receiver(user_logged_in)
@receiver(user_logged_out)
def enforce_single_login(sender, request, user, signal, **kwargs):  # pylint: disable=unused-argument
    """
    Sets the current session id in the user profile,
    to prevent concurrent logins.
    """
    if settings.FEATURES.get('PREVENT_CONCURRENT_LOGINS', False):
        if signal == user_logged_in:
            key = request.session.session_key
        else:
            key = None
        if user:
            user_profile, __ = UserProfile.objects.get_or_create(
                user=user,
                defaults={'name': user.username}
            )
            if user_profile:
                user.profile.set_login_session(key)


class DashboardConfiguration(ConfigurationModel):
    """
    Note:
        This model is deprecated and we should not be adding new content to it.
        We will eventually migrate this one entry to a django setting as well.

    Dashboard Configuration settings.

    Includes configuration options for the dashboard, which impact behavior and rendering for the application.

    .. no_pii:
    """
    recent_enrollment_time_delta = models.PositiveIntegerField(
        default=0,
        help_text="The number of seconds in which a new enrollment is considered 'recent'. "
                  "Used to display notifications."
    )

    @property
    def recent_enrollment_seconds(self):
        return self.recent_enrollment_time_delta


class LinkedInAddToProfileConfiguration(ConfigurationModel):
    """
    LinkedIn Add to Profile Configuration

    This configuration enables the 'Add to Profile' LinkedIn button. The button
    appears when users have a certificate available; when clicked, users are sent
    to the LinkedIn site with a pre-filled form allowing them to add the
    certificate to their LinkedIn profile.

    See https://addtoprofile.linkedin.com/ for documentation on parameters

    .. no_pii:
    """

    MODE_TO_CERT_NAME = {
        'honor': _('{platform_name} Honor Code Certificate for {course_name}'),
        'verified': _('{platform_name} Verified Certificate for {course_name}'),
        'professional': _('{platform_name} Professional Certificate for {course_name}'),
        'no-id-professional': _('{platform_name} Professional Certificate for {course_name}'),
    }

    company_identifier = models.TextField(
        blank=True,
        help_text=_(
            'Your organization ID (if your organization has an existing page on LinkedIn) e.g 1337. '
            'If not provided, will default to sending Platform Name (e.g. edX) instead.'
        ),
    )

    @property
    def share_settings(self):
        """
        Initialize share_settings once for reuse across methods
        """
        if self._share_settings is None:
            self._share_settings = configuration_helpers.get_value(
                'SOCIAL_SHARING_SETTINGS',
                settings.SOCIAL_SHARING_SETTINGS
            )
        return self._share_settings

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._share_settings = None

    def is_enabled(self, *key_fields):  # pylint: disable=arguments-differ
        """
        Checks both the model itself and share_settings to see if LinkedIn Add to Profile is enabled
        """
        enabled = super().is_enabled(*key_fields)
        return self.share_settings.get('CERTIFICATE_LINKEDIN', enabled)

    def add_to_profile_url(self, course, cert_mode, cert_url, certificate=None):

        """
        Construct the URL for the "add to profile" button. This will autofill the form based on
        the params provided.

        Arguments:
            course (CourseOverview): Course/CourseOverview Object.
            cert_mode (str): The course mode of the user's certificate (e.g. "verified", "honor", "professional")
            cert_url (str): The URL for the certificate.

        Keyword Arguments:
            certificate (GeneratedCertificate): a GeneratedCertificate object for the user and course.
                If provided, this function will also autofill the certId and issue date for the cert.
        """
        params = {
            'name': self._cert_name(course.display_name, cert_mode),
            'certUrl': cert_url,
        }

        params.update(self._organization_information(course))

        if certificate:
            params.update({
                'certId': certificate.verify_uuid,
                'issueYear': certificate.created_date.year,
                'issueMonth': certificate.created_date.month,
            })

        return 'https://www.linkedin.com/profile/add?startTask=CERTIFICATION_NAME&{params}'.format(
            params=urlencode(params)
        )

    def _cert_name(self, course_name, cert_mode):
        """
        Name of the certification, for display on LinkedIn.

        Arguments:
            course_name (unicode): The display name of the course.
            cert_mode (str): The course mode of the user's certificate (e.g. "verified", "honor", "professional")

        Returns:
            str: The formatted string to display for the name field on the LinkedIn Add to Profile dialog.
        """
        default_cert_name = self.MODE_TO_CERT_NAME.get(
            cert_mode, _('{platform_name} Certificate for {course_name}')
        )
        # Look for an override of the certificate name in the SOCIAL_SHARING_SETTINGS setting
        cert_name = self.share_settings.get(
            'CERTIFICATE_LINKEDIN_MODE_TO_CERT_NAME', {}
        ).get(cert_mode, default_cert_name)

        return cert_name.format(
            platform_name=configuration_helpers.get_value('platform_name', settings.PLATFORM_NAME),
            course_name=course_name
        )

    def _organization_information(self, course=None):
        """
        Returns organization information for use in the URL parameters for add to
        profile. By default when sharing to LinkedIn, Platform Name and/or Platform
        LINKEDIN_COMPANY_ID will be used. If Course specific Organization Name is
        prefered when sharing Certificate to linkedIn the flag for that
        CERTIFICATE_LINKEDIN_DEFAULTS_TO_COURSE_ORGANIZATION_NAME should be set
        to True alongside other LinkedIn settings

        Returns:
            dict: Either the organization ID on LinkedIn, the organization's name or
                organization name associated to a specific course
                Will be used to prefill the organization on the add to profile action.
        """
        prefer_course_organization_name = self.share_settings.get(
            'CERTIFICATE_LINKEDIN_DEFAULTS_TO_COURSE_ORGANIZATION_NAME', False
        )
        if (prefer_course_organization_name and course):
            return {"organizationName": course.display_organization}

        org_id = configuration_helpers.get_value(
            "LINKEDIN_COMPANY_ID", self.company_identifier
        )
        # Prefer organization ID per documentation at https://addtoprofile.linkedin.com/
        if org_id:
            return {"organizationId": org_id}
        return {'organizationName': configuration_helpers.get_value('platform_name', settings.PLATFORM_NAME)}


class EntranceExamConfiguration(models.Model):
    """
    Represents a Student's entrance exam specific data for a single Course

    .. no_pii:
    """

    user = models.ForeignKey(User, db_index=True, on_delete=models.CASCADE)
    course_id = CourseKeyField(db_index=True)
    created = models.DateTimeField(auto_now_add=True, null=True, db_index=True)
    updated = models.DateTimeField(auto_now=True, db_index=True)

    # if skip_entrance_exam is True, then student can skip entrance exam
    # for the course
    skip_entrance_exam = models.BooleanField(default=True)

    class Meta:
        unique_together = (('user', 'course_id'), )

    def __str__(self):
        return "[EntranceExamConfiguration] {}: {} ({}) = {}".format(
            self.user, self.course_id, self.created, self.skip_entrance_exam
        )

    @classmethod
    def user_can_skip_entrance_exam(cls, user, course_key):
        """
        Return True if given user can skip entrance exam for given course otherwise False.
        """
        can_skip = False
        if ENTRANCE_EXAMS.is_enabled():
            try:
                record = EntranceExamConfiguration.objects.get(user=user, course_id=course_key)
                can_skip = record.skip_entrance_exam
            except EntranceExamConfiguration.DoesNotExist:
                can_skip = False
        return can_skip


class LanguageField(models.CharField):
    """Represents a language from the ISO 639-1 language set."""

    def __init__(self, *args, **kwargs):
        """Creates a LanguageField.

        Accepts all the same kwargs as a CharField, except for max_length and
        choices. help_text defaults to a description of the ISO 639-1 set.
        """
        kwargs.pop('max_length', None)
        kwargs.pop('choices', None)
        help_text = kwargs.pop(
            'help_text',
            _("The ISO 639-1 language code for this language."),
        )
        super().__init__(
            max_length=16,
            choices=settings.ALL_LANGUAGES,
            help_text=help_text,
            *args,
            **kwargs
        )


class LanguageProficiency(models.Model):
    """
    Represents a user's language proficiency.

    Note that we have not found a way to emit analytics change events by using signals directly on this
    model or on UserProfile. Therefore if you are changing LanguageProficiency values, it is important
    to go through the accounts API (AccountsView) defined in
    /edx-platform/openedx/core/djangoapps/user_api/accounts/views.py or its associated api method
    (update_account_settings) so that the events are emitted.

    .. no_pii: Language is not PII value according to OEP-30.
    """
    class Meta:
        unique_together = (('code', 'user_profile'),)

    user_profile = models.ForeignKey(UserProfile, db_index=True, related_name='language_proficiencies',
                                     on_delete=models.CASCADE)
    code = models.CharField(
        max_length=16,
        blank=False,
        choices=settings.ALL_LANGUAGES,
        help_text=_("The ISO 639-1 language code for this language.")
    )


class SocialLink(models.Model):
    """
    Represents a URL connecting a particular social platform to a user's social profile.

    The platforms are listed in the lms/common.py file under SOCIAL_PLATFORMS.
    Each entry has a display name, a url_stub that describes a required
    component of the stored URL and an example of a valid URL.

    The stored social_link value must adhere to the form 'https://www.[url_stub][username]'.

    .. pii: Stores linkage from User to a learner's social media profiles. Retired in AccountRetirementView.
    .. pii_types: external_service
    .. pii_retirement: local_api
    """
    user_profile = models.ForeignKey(UserProfile, db_index=True, related_name='social_links', on_delete=models.CASCADE)
    platform = models.CharField(max_length=30)
    social_link = models.CharField(max_length=100, blank=True)


class RegistrationCookieConfiguration(ConfigurationModel):
    """
    Configuration for registration cookies.

    .. no_pii:
    """
    utm_cookie_name = models.CharField(
        max_length=255,
        help_text=_("Name of the UTM cookie")
    )

    affiliate_cookie_name = models.CharField(
        max_length=255,
        help_text=_("Name of the affiliate cookie")
    )

    def __str__(self):
        """Unicode representation of this config. """
        return "UTM: {utm_name}; AFFILIATE: {affiliate_name}".format(
            utm_name=self.utm_cookie_name,
            affiliate_name=self.affiliate_cookie_name
        )


class UserAttribute(TimeStampedModel):
    """
    Record additional metadata about a user, stored as key/value pairs of text.

    .. no_pii:
    """

    class Meta:
        # Ensure that at most one value exists for a given user/name.
        unique_together = (('user', 'name',), )

    user = models.ForeignKey(User, related_name='attributes', on_delete=models.CASCADE)
    name = models.CharField(max_length=255, help_text=_("Name of this user attribute."), db_index=True)
    value = models.CharField(max_length=255, help_text=_("Value of this user attribute."))

    def __str__(self):
        return "[{username}] {name}: {value}".format(
            name=self.name,
            value=self.value,
            username=self.user.username
        )

    @classmethod
    def set_user_attribute(cls, user, name, value):
        """
        Add an name/value pair as an attribute for the given
        user. Overwrites any previous value for that name, if it
        exists.
        """
        cls.objects.update_or_create(user=user, name=name, defaults={'value': value})

    @classmethod
    def get_user_attribute(cls, user, name):
        """
        Return the attribute value for the given user and name. If no such
        value exists, returns None.
        """
        try:
            return cls.objects.get(user=user, name=name).value
        except cls.DoesNotExist:
            return None


class AccountRecoveryManager(models.Manager):
    """
    Custom Manager for AccountRecovery model
    """

    def get_active(self, **filters):
        """
        Return only active AccountRecovery record after applying the given filters.

        Arguments:
            filters (**kwargs): Filter parameters for AccountRecovery records.

        Returns:
            AccountRecovery: AccountRecovery object with is_active=true
        """
        filters['is_active'] = True
        return super().get_queryset().get(**filters)

    def activate(self):
        """
        Set is_active flag to True.
        """
        super().get_queryset().update(is_active=True)


class AccountRecovery(models.Model):
    """
    Model for storing information for user's account recovery in case of access loss.

    .. pii: the field named secondary_email contains pii, retired in the `DeactivateLogoutView`
    .. pii_types: email_address
    .. pii_retirement: local_api
    """
    user = models.OneToOneField(User, related_name='account_recovery', on_delete=models.CASCADE)
    secondary_email = models.EmailField(
        verbose_name=_('Secondary email address'),
        help_text=_('Secondary email address to recover linked account.'),
        unique=True,
        null=False,
        blank=False,
    )
    is_active = models.BooleanField(default=False)

    class Meta:
        db_table = "auth_accountrecovery"

    objects = AccountRecoveryManager()

    def update_recovery_email(self, email):
        """
        Update the secondary email address on the instance to the email in the argument.

        Arguments:
            email (str): New email address to be set as the secondary email address.
        """
        self.secondary_email = email
        self.is_active = True
        self.save()

    @classmethod
    def retire_recovery_email(cls, user_id):
        """
        Retire user's recovery/secondary email as part of GDPR Phase I.
        Returns 'True'

        If an AccountRecovery record is found for this user it will be deleted,
        if it is not found it is assumed this table has no PII for the given user.

        :param user_id: int
        :return: bool
        """
        try:
            cls.objects.get(user_id=user_id).delete()
        except cls.DoesNotExist:
            pass

        return True


class AllowedAuthUser(TimeStampedModel):
    site = models.ForeignKey(Site, related_name='allowed_auth_users', on_delete=models.CASCADE)
    email = models.EmailField(
        help_text=_(
            "An employee (a user whose email has current site's domain name) whose email exists in this model, can be "
            "able to login from login screen through email and password. And if any employee's email doesn't exist in "
            "this model then that employee can login via third party authentication backend only."),
        unique=True,
    )


class AccountRecoveryConfiguration(ConfigurationModel):
    """
    configuration model for recover account management command

    .. no_pii:
    """
    csv_file = models.FileField(
        validators=[FileExtensionValidator(allowed_extensions=['csv'])],
        help_text=_("It expect that the data will be provided in a csv file format with \
                    first row being the header and columns will be as follows: \
                    username, current_email, desired_email")
    )


class UserCelebration(TimeStampedModel):
    """
    Keeps track of how we've celebrated a user's progress on the platform.
    This class is for course agnostic celebrations (not specific to a particular enrollment).
    CourseEnrollmentCelebration is for celebrations that happen separately for each separate course.

    .. no_pii:
    """
    user = models.OneToOneField(User, models.CASCADE, related_name='celebration')
    # The last_day_of_streak and streak_length fields are used to
    # control celebration of the streak feature.
    # A streak is when a learner visits the learning MFE N days in a row.
    # The business logic of streaks for a 3 day streak and 1 day break is the following:
    # 1. Each streak should be celebrated exactly once, once the learner has completed the streak.
    # 2. If a learner misses enough days to count as a break, the streak resets back to 0.
    # 3. The streak is measured against the learner's configured timezone
    # 4. We keep track of the total length of the streak, so there is a possibility in the future
    # to add multiple celebrations for longer streaks.
    # 5. We keep track of the longest_ever_streak field for potential future use for badging purposes.
    last_day_of_streak = models.DateField(default=None, null=True, blank=True)
    streak_length = models.IntegerField(default=0)
    longest_ever_streak = models.IntegerField(default=0)
    STREAK_LENGTHS_TO_CELEBRATE = [3]
    STREAK_BREAK_LENGTH = 1

    def __str__(self):
        return (
            '[UserCelebration] user: {}; last_day_of_streak {}; streak_length {}; longest_ever_streak {};'
        ).format(self.user.username, self.last_day_of_streak, self.streak_length, self.longest_ever_streak)

    @classmethod
    def _get_now(cls, browser_timezone):
        """ Retrieve the value for the current datetime in the user's timezone

        Once a user visits the learning MFE, their streak will not increment until midnight in their timezone.
        The decision was to use the user's timezone and not UTC, to make each day of the streak more closely
        correspond to separate days for the user.
        The learning MFE passes in the browser timezone which is used as a fallback option if the user's timezone
        in their account is not set.
        UTC is used as a final fallback if neither timezone is set.
        """
        # importing here to avoid a circular import
        from lms.djangoapps.courseware.context_processor import user_timezone_locale_prefs
        user_timezone_locale = user_timezone_locale_prefs(crum.get_current_request())
        user_timezone = timezone(user_timezone_locale['user_timezone'] or browser_timezone or str(UTC))
        return user_timezone.localize(datetime.now())

    def _calculate_streak_updates(self, today):
        """ Calculate the updates that should be applied to the streak fields of the provided celebration
        A streak is incremented once for each day that a learner accesses the learning MFE.
        A break is the amount of time that needs to pass before we stop incrementing the
        existing streak and start a brand new streak.
        See the UserCelebrationTests class for examples that should help clarify this behavior.
        """
        last_day_of_streak = self.last_day_of_streak
        streak_length = self.streak_length
        streak_length_to_celebrate = None

        first_ever_streak = last_day_of_streak is None
        break_length = timedelta(days=self.STREAK_BREAK_LENGTH)
        should_start_new_streak = last_day_of_streak and last_day_of_streak + break_length < today
        already_updated_streak_today = last_day_of_streak == today

        last_day_of_streak = today
        if first_ever_streak or should_start_new_streak:
            # Start new streak
            streak_length = 1
        elif not already_updated_streak_today:
            streak_length += 1
            if streak_length in self.STREAK_LENGTHS_TO_CELEBRATE:
                # Celebrate if we didn't already celebrate today
                streak_length_to_celebrate = streak_length

        return last_day_of_streak, streak_length, streak_length_to_celebrate, already_updated_streak_today

    def _update_streak(self, last_day_of_streak, streak_length):
        """ Update the celebration with the new streak data """
        # If anything needs to be updated, update the celebration in the database
        if last_day_of_streak != self.last_day_of_streak:
            self.last_day_of_streak = last_day_of_streak
            self.streak_length = streak_length
            self.longest_ever_streak = max(self.longest_ever_streak, streak_length)

            self.save()

    def _emit_streak_update_event(self, user: User, course_id: str, current_streak_length: int) -> None:
        """
        Emits a server-side event using the event tracking library with details about the learner's current streak. The
        course run ID is included to enable tracking of progress trends over time.

        Args:
            user (User): The user whose streak is being updated.
            course_id (str): The course run ID the learner is currently engaged with.
            current_streak_length (int): The number of consecutive days the user has been active.
        """
        context = {
            "user_id": user.id,
            "course_id": course_id,
        }
        data = {
            "user_id": user.id,
            "current_course_id": course_id,
            "current_streak_length": current_streak_length,
        }

        with tracker.get_tracker().context(USER_STREAK_UPDATED_EVENT_NAME, context):
            tracker.emit(
                USER_STREAK_UPDATED_EVENT_NAME,
                data,
            )

    @classmethod
    def _get_celebration(cls, user, course_key):
        """ Retrieve (or create) the celebration for the provided user and course_key """
        try:
            # Only enable the streak if milestones and the streak are enabled for this course
            if not streak_celebration_is_active(course_key):
                return None
            return user.celebration
        except (cls.DoesNotExist, User.celebration.RelatedObjectDoesNotExist):  # pylint: disable=no-member
            celebration, _ = UserCelebration.objects.get_or_create(user=user)
            return celebration

    @classmethod
    def perform_streak_updates(cls, user, course_key, browser_timezone=None):
        """
        Determine if the user should see a streak celebration and return the length of the streak the user should
        celebrate.

        Additionally, we record any updates to the current streak in the database and emit a server side
        event about the update.

        Args:
            user (User): The user whose streak is being updated.
            course_key (CourseLocator): The Course Run key of the course the user is currently engaged with when
                recording the streak update.
            browser_timezone (str): String representing the current time zone set from a user's web browser.
                May be null.

        Returns:
            streak_length_to_celebrate (int): A number representing how many days in a row a learner has been actively
                engaging in learning content in the courseware.
        """
        # importing here to avoid a circular import
        from lms.djangoapps.courseware.masquerade import is_masquerading_as_specific_student
        if (
            not user
            or user.is_anonymous
            or is_masquerading_as_specific_student(user, course_key)
        ):
            return None

        celebration = cls._get_celebration(user, course_key)
        if not celebration:
            return None

        today = cls._get_now(browser_timezone).date()
        # pylint: disable=protected-access
        last_day_of_streak, streak_length, streak_length_to_celebrate, already_updated_streak_today = \
            celebration._calculate_streak_updates(today)
        # pylint: enable=protected-access

        if not already_updated_streak_today:
            cls._update_streak(celebration, last_day_of_streak, streak_length)
            cls._emit_streak_update_event(celebration, user, str(course_key), streak_length)

        return streak_length_to_celebrate


class UserPasswordToggleHistory(TimeStampedModel):
    """
    Keeps track of user password disable/enable history

    .. no_pii:
    """
    user = models.ForeignKey(User, related_name='password_toggle_history', on_delete=models.CASCADE)
    comment = models.CharField(max_length=255, help_text=_("Add a reason"), blank=True, null=True)
    disabled = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)

    class Meta:
        ordering = ['-created']

    def __str__(self):
        return self.comment


@receiver(pre_save, sender=CourseAccessRole)
def pre_save_course_access_role(sender, instance, **kwargs):
    """
    Captures the current state of a CourseAccessRole before it is saved for update tracking.
    """
    if instance.pk:
        try:
            old_instance = sender.objects.get(pk=instance.pk)
            # pylint: disable=protected-access
            instance._old_values = {
                'user_id': old_instance.user_id,
                'org': old_instance.org,
                'course_id': str(old_instance.course_id) if old_instance.course_id else None,
                'role': old_instance.role,
            }
        except sender.DoesNotExist:
            # pylint: disable=protected-access
            instance._old_values = None


@receiver(post_save, sender=CourseAccessRole)
def create_course_access_role_history_on_save(sender, instance, created, **kwargs):
    """
    Handle create and update actions for CourseAccessRole objects.
    """
    action_type = 'created' if created else 'updated'
    current_user = crum.get_current_user()
    old_values = getattr(instance, '_old_values', None) if not created else None
    CourseAccessRoleHistory.objects.create(
        user=instance.user,
        org=instance.org,
        course_id=instance.course_id,
        role=instance.role,
        action_type=action_type,
        changed_by=current_user if current_user and current_user.is_authenticated else None,
        old_values=old_values
    )


@receiver(post_delete, sender=CourseAccessRole)
def create_course_access_role_history_on_delete(sender, instance, **kwargs):
    """
    Handle delete actions for CourseAccessRole objects.
    """
    current_user = crum.get_current_user()
    CourseAccessRoleHistory.objects.create(
        user=instance.user,
        org=instance.org,
        course_id=instance.course_id,
        role=instance.role,
        action_type='deleted',
        changed_by=current_user if current_user and current_user.is_authenticated else None
    )
