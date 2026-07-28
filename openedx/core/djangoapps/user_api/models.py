"""
Django ORM model specifications for the User API application
"""


from config_models.models import ConfigurationModel
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.validators import FileExtensionValidator, RegexValidator
from django.db import models
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _
from model_utils.models import TimeStampedModel
from opaque_keys.edx.django.models import CourseKeyField

# pylint: disable=unused-import
from common.djangoapps.student.models import get_retired_email_by_email, get_retired_username_by_username
from common.djangoapps.util.model_utils import emit_settings_changed_event, get_changed_fields_dict
from openedx.core.djangolib.model_mixins import DeletableByUserValue
from openedx.core.lib.cache_utils import request_cached

# Currently, the "student" app is responsible for
# accounts, profiles, enrollments, and the student dashboard.
# We are trying to move some of this functionality into separate apps,
# but currently the rest of the system assumes that "student" defines
# certain models.  For now we will leave the models in "student" and
# create an alias in "user_api".



class RetirementStateError(Exception):
    pass


class UserPreference(models.Model):  # noqa: DJ008
    """
    A user's preference, stored as generic text to be processed by client

    .. no_pii: Stores arbitrary key/value pairs, currently none are PII. If that changes, update this annotation.
    """
    KEY_REGEX = "[-_a-zA-Z0-9]+"
    user = models.ForeignKey(User, db_index=True, related_name="preferences", on_delete=models.CASCADE)
    key = models.CharField(max_length=255, db_index=True, validators=[RegexValidator(KEY_REGEX)])
    value = models.TextField()

    class Meta:
        unique_together = ("user", "key")

    @staticmethod
    @request_cached()
    def get_all_preferences(user):
        """
        Gets all preferences for a given user

        Returns: Set of (preference type, value) pairs for each of the user's preferences
        """
        return {pref.key: pref.value for pref in user.preferences.all()}

    @classmethod
    def get_value(cls, user, preference_key, default=None):
        """Gets the user preference value for a given key.

        Note:
            This method provides no authorization of access to the user preference.
            Consider using user_api.preferences.api.get_user_preference instead if
            this is part of a REST API request.

        Arguments:
            user (User): The user whose preference should be set.
            preference_key (str): The key for the user preference.
            default: The object to return if user does not have preference key set

        Returns:
            The user preference value, or default if one is not set.
        """
        try:
            user_preference = cls.objects.get(user=user, key=preference_key)
            return user_preference.value
        except cls.DoesNotExist:
            return default

    @classmethod
    def has_value(cls, user, preference_key):
        """Checks if the user has preference value for a given key.

        Note:
            This method provides no authorization of access to the user preference.
            Consider using user_api.preferences.api.has_user_preference instead if
            this is part of a REST API request.

        Arguments:
            user (User): The user whose preference should be checked.
            preference_key (str): The key for the user preference.

        Returns:
            (bool): True if user preference for the given key is set and False otherwise.
        """
        return cls.objects.filter(user=user, key=preference_key).exists()

    @classmethod
    def get_preference_for_users(cls, user_ids, preference_key):
        """
        Returns preference for list of users
        """
        return cls.objects.filter(user__in=user_ids, key=preference_key)


@receiver(pre_save, sender=UserPreference)
def pre_save_callback(sender, **kwargs):
    """
    Event changes to user preferences.
    """
    user_preference = kwargs["instance"]
    user_preference._old_value = get_changed_fields_dict(user_preference, sender).get("value", None)  # pylint: disable=protected-access


@receiver(post_save, sender=UserPreference)
def post_save_callback(sender, **kwargs):
    """
    Event changes to user preferences.
    """

    user_preference = kwargs["instance"]
    emit_settings_changed_event(
        user_preference.user, sender._meta.db_table,
        {
            user_preference.key: (
                user_preference._old_value,  # pylint: disable=protected-access
                user_preference.value
            )
        }
    )
    user_preference._old_value = None  # pylint: disable=protected-access


@receiver(post_delete, sender=UserPreference)
def post_delete_callback(sender, **kwargs):
    """
    Event changes to user preferences.
    """
    user_preference = kwargs["instance"]
    emit_settings_changed_event(
        user_preference.user, sender._meta.db_table, {
            user_preference.key: (user_preference.value, None)
        }
    )


class UserCourseTag(models.Model):  # noqa: DJ008
    """
    Per-course user tags, to be used by various things that want to store tags about
    the user.  Added initially to store assignment to experimental groups.

    .. no_pii: Stores arbitrary key/value pairs about users, but does not currently store any PII. This may change!
    """
    user = models.ForeignKey(User, db_index=True, related_name="+", on_delete=models.CASCADE)
    key = models.CharField(max_length=255, db_index=True)
    course_id = CourseKeyField(max_length=255, db_index=True)
    value = models.TextField()

    class Meta:
        unique_together = ("user", "course_id", "key")


class UserOrgTag(TimeStampedModel, DeletableByUserValue):
    """
    Per-Organization user tags.

    Allows settings to be configured at an organization level.

    .. pii: Does not strictly store PII, but maintains the email-optin flag and so is retired in AccountRetirementView.
    .. pii_types: other
    .. pii_retirement: local_api
    """
    user = models.ForeignKey(User, db_index=True, related_name="+", on_delete=models.CASCADE)
    key = models.CharField(max_length=255, db_index=True)
    org = models.CharField(max_length=255, db_index=True)
    value = models.TextField()

    class Meta:
        unique_together = ("user", "org", "key")


class RetirementState(models.Model):
    """
    Stores the list and ordering of the steps of retirement, this should almost never change
    as updating it can break the retirement process of users already in the queue.

    .. no_pii:
    """
    state_name = models.CharField(max_length=30, unique=True)
    state_execution_order = models.SmallIntegerField(unique=True)
    is_dead_end_state = models.BooleanField(default=False, db_index=True)
    required = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.state_name} (step {self.state_execution_order})'

    class Meta:  # noqa: DJ012
        ordering = ('state_execution_order',)

    @classmethod
    def get_dead_end_states(cls):
        # We use models.Value(1) to make use of the indexing on the field. MySQL does not
        # support boolean types natively, and checking for False will cause a table scan.
        return cls.objects.filter(is_dead_end_state=models.Value(1))

    @classmethod
    def get_dead_end_state_names_list(cls):
        # We use models.Value(0) to make use of the indexing on the field. MySQL does not
        # support boolean types natively, and checking for False will cause a table scan.
        return cls.objects.filter(is_dead_end_state=models.Value(1)).values_list('state_name', flat=True)

    @classmethod
    def get_state_names_list(cls):
        return cls.objects.all().values_list('state_name', flat=True)


class UserRetirementPartnerReportingStatus(TimeStampedModel):
    """
    When a user has been retired from LMS it will still need to be reported out to
    partners so they can forget the user also. This process happens on a very different,
    and asynchronous, timeline than LMS retirement and only impacts a subset of learners
    so it maintains a queue. This queue is populated as part of the LMS retirement
    process.

    .. pii: Contains a retiring user's name, username, and email. Retired in AccountRetirementPartnerReportView.
    .. pii_types: name, username, email_address
    .. pii_retirement: local_api
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    original_username = models.CharField(max_length=150, db_index=True)
    original_email = models.EmailField(db_index=True)
    original_name = models.CharField(max_length=255, blank=True, db_index=True)
    is_being_processed = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'User Retirement Reporting Status'
        verbose_name_plural = 'User Retirement Reporting Statuses'

    def __str__(self):
        return 'UserRetirementPartnerReportingStatus: {} is being processed: {}'.format(  # noqa: UP032
            self.user,
            self.is_being_processed
        )


class UserRetirementRequest(TimeStampedModel):
    """
    Records and perists every user retirement request.
    Users that have requested to cancel their retirement before retirement begins can be removed.
    All other retired users persist in this table forever.

    .. no_pii:
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    class Meta:
        verbose_name = 'User Retirement Request'
        verbose_name_plural = 'User Retirement Requests'

    @classmethod
    def create_retirement_request(cls, user):
        """
        Creates a UserRetirementRequest for the specified user.
        """
        if cls.has_user_requested_retirement(user):
            raise RetirementStateError(f'User {user} already has a retirement request row!')
        return cls.objects.create(user=user)

    @classmethod
    def has_user_requested_retirement(cls, user):
        """
        Checks to see if a UserRetirementRequest has been created for the specified user.
        """
        return cls.objects.filter(user=user).exists()

    def __str__(self):
        return f'User: {self.user.id} Requested: {self.created}'


class UserRetirementStatus(TimeStampedModel):
    """
    Tracks the progress of a user's retirement request

    .. pii: Contains a retiring user's name, username, and email. Retired in AccountRetirementStatusView.cleanup().
    .. pii_types: name, username, email_address
    .. pii_retirement: local_api
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    original_username = models.CharField(max_length=150, db_index=True)
    original_email = models.EmailField(db_index=True)
    original_name = models.CharField(max_length=255, blank=True, db_index=True)
    retired_username = models.CharField(max_length=150, db_index=True)
    retired_email = models.EmailField(db_index=True)
    current_state = models.ForeignKey(RetirementState, related_name='current_state', on_delete=models.CASCADE)
    last_state = models.ForeignKey(RetirementState, blank=True, related_name='last_state', on_delete=models.CASCADE)
    responses = models.TextField()

    class Meta:
        verbose_name = 'User Retirement Status'
        verbose_name_plural = 'User Retirement Statuses'

    def _validate_state_update(self, new_state):
        """
        Confirm that the state move that's trying to be made is allowed
        """
        dead_end_states = list(RetirementState.get_dead_end_state_names_list())
        states = list(RetirementState.get_state_names_list())
        if self.current_state in dead_end_states:
            raise RetirementStateError(f'RetirementStatus: Unable to move user from {self.current_state}')

        try:
            new_state_index = states.index(new_state)
            if new_state_index <= states.index(self.current_state.state_name):
                raise ValueError()
        except ValueError:
            err = f'{new_state} does not exist or is an eariler state than current state {self.current_state}'
            raise RetirementStateError(err)  # pylint: disable=raise-missing-from  # noqa: B904

    def _validate_update_data(self, data):
        """
        Confirm that the data passed in is properly formatted
        """
        required_keys = ('username', 'new_state', 'response')
        optional_keys = ('force', )
        known_keys = required_keys + optional_keys

        for required_key in required_keys:
            if required_key not in data:
                raise RetirementStateError(f'RetirementStatus: Required key {required_key} missing from update')

        for key in data:
            if key not in known_keys:
                raise RetirementStateError(f'RetirementStatus: Unknown key {key} in update')

    @classmethod
    def create_retirement(cls, user):
        """
        Creates a UserRetirementStatus for the given user, in the correct initial state. Will
        fail if the user already has a UserRetirementStatus row or if states are not yet populated.
        """
        try:
            pending = RetirementState.objects.all().order_by('state_execution_order')[0]
        except IndexError:
            raise RetirementStateError('Default state does not exist! Populate retirement states to retire users.')  # pylint: disable=raise-missing-from,line-too-long  # noqa: B904

        if cls.objects.filter(user=user).exists():
            raise RetirementStateError(f'User {user} already has a retirement status row!')

        retired_username = get_retired_username_by_username(user.username)
        retired_email = get_retired_email_by_email(user.email)

        UserRetirementRequest.create_retirement_request(user)

        return cls.objects.create(
            user=user,
            original_username=user.username,
            original_email=user.email,
            original_name=user.profile.name,
            retired_username=retired_username,
            retired_email=retired_email,
            current_state=pending,
            last_state=pending,
            responses=f'Created in state {pending} by create_retirement'
        )

    def update_state(self, update):
        """
        Perform the necessary checks for a state change and update the state and response if passed
        or throw a RetirementStateError with a useful error message
        """
        self._validate_update_data(update)

        force = update.get('force', False)
        if not force:
            self._validate_state_update(update['new_state'])

        old_state = self.current_state
        self.current_state = RetirementState.objects.get(state_name=update['new_state'])
        self.last_state = old_state
        self.responses += "\n Moved from {} to {}:\n{}\n".format(old_state, self.current_state, update['response'])
        self.save()

    @classmethod
    def get_retirement_for_retirement_action(cls, username):
        """
        Convenience method to get a UseRetirementStatus for a particular user with some checking
        to make sure they're in a state that is acceptable to be acted upon. The user should be
        in a "working state" (not a dead end state, PENDING, or *_COMPLETE). This should help
        a bit with situations like the retirement driver accidentally trying to act upon the
        same user twice at the same time, or trying to take action on an errored user.

        Can raise UserRetirementStatus.DoesNotExist or RetirementStateError, otherwise should
        return a UserRetirementStatus
        """
        # During a narrow window learners were able to re-use a username that had been retired if
        # they altered the capitalization of one or more characters. Therefore we can have more
        # than one row returned here (due to our MySQL collation being case-insensitive), and need
        # to disambiguate them in Python, which will respect case in the comparison.
        retirements = cls.objects.filter(original_username=username)

        retirement = None
        for r in retirements:
            if r.original_username == username:
                retirement = r
                break

        if retirement is None:
            raise UserRetirementStatus.DoesNotExist('{} does not have an exact match in UserRetirementStatus. '  # noqa: UP032  # pylint: disable=line-too-long
                                                    '{} similar rows found.'.format(username, len(retirements)))

        state = retirement.current_state

        if state.required or state.state_name.endswith('_COMPLETE'):
            raise RetirementStateError('{} is in {}, not a valid state to perform retirement '  # noqa: UP032
                                       'actions on.'.format(retirement, state.state_name))

        return retirement

    def __str__(self):
        return f'User: {self.user.id} State: {self.current_state} Last Updated: {self.modified}'


class BulkUserRetirementConfig(ConfigurationModel):
    """
    Configuration to store a csv file that will be used in retire_user management command.

    .. no_pii:
    """
    # Timeout set to 0 so that the model does not read from cached config in case the config entry is deleted.
    cache_timeout = 0
    csv_file = models.FileField(
        upload_to="bulk_user_retirement_files/",
        validators=[FileExtensionValidator(allowed_extensions=['csv'])],
        help_text=_("Comma separated file that have username and user_email of the users that needs to be retired")
    )


@receiver(models.signals.post_delete, sender=UserRetirementStatus)
def remove_pending_retirement_request(sender, instance, **kwargs):   # pylint: disable=unused-argument
    """
    Whenever a UserRetirementStatus record is deleted, remove the user's UserRetirementRequest record
    IFF the UserRetirementStatus record was still PENDING.
    """
    pending_state = RetirementState.objects.filter(state_name='PENDING')[0]
    if pending_state and instance.current_state == pending_state:
        UserRetirementRequest.objects.filter(user=instance.user).delete()
