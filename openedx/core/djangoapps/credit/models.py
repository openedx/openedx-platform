"""
Models for Credit Eligibility for courses.

Credit courses allow students to receive university credit for
successful completion of a course on EdX
"""


import datetime
import logging
from collections import defaultdict
from zoneinfo import ZoneInfo

from config_models.models import ConfigurationModel
from django.conf import settings
from django.core.cache import cache
from django.core.validators import RegexValidator
from django.db import IntegrityError, models, transaction
from django.dispatch import receiver
from django.utils.translation import gettext as _  # pylint: disable=unused-import  # noqa: F401
from django.utils.translation import gettext_lazy
from edx_django_utils.cache import RequestCache
from jsonfield.fields import JSONField
from model_utils.models import TimeStampedModel
from opaque_keys.edx.django.models import CourseKeyField

from openedx.core.lib.cache_utils import request_cached

CREDIT_PROVIDER_ID_REGEX = "[a-z,A-Z,0-9,\\-]+"
log = logging.getLogger(__name__)


class CreditProvider(TimeStampedModel):
    """
    This model represents an institution that can grant credit for a course.

    Each provider is identified by unique ID (e.g., 'ASU'). CreditProvider also
    includes a `url` where the student will be sent when he/she will try to
    get credit for course. Eligibility duration will be use to set duration
    for which credit eligible message appears on dashboard.

    .. no_pii:
    """
    provider_id = models.CharField(
        max_length=255,
        unique=True,
        validators=[
            RegexValidator(
                regex=CREDIT_PROVIDER_ID_REGEX,
                message="Only alphanumeric characters and hyphens (-) are allowed",
                code="invalid_provider_id",
            )
        ],
        help_text=gettext_lazy(
            "Unique identifier for this credit provider. "
            "Only alphanumeric characters and hyphens (-) are allowed. "
            "The identifier is case-sensitive."
        )
    )

    active = models.BooleanField(
        default=True,
        help_text=gettext_lazy("Whether the credit provider is currently enabled.")
    )

    display_name = models.CharField(
        max_length=255,
        help_text=gettext_lazy("Name of the credit provider displayed to users")
    )

    enable_integration = models.BooleanField(
        default=False,
        help_text=gettext_lazy(
            "When true, automatically notify the credit provider "
            "when a user requests credit. "
            "In order for this to work, a shared secret key MUST be configured "
            "for the credit provider in secure auth settings."
        )
    )

    provider_url = models.URLField(
        default="",
        help_text=gettext_lazy(
            "URL of the credit provider.  If automatic integration is "
            "enabled, this will the the end-point that we POST to "
            "to notify the provider of a credit request.  Otherwise, the "
            "user will be shown a link to this URL, so the user can "
            "request credit from the provider directly."
        )
    )

    provider_status_url = models.URLField(
        default="",
        help_text=gettext_lazy(
            "URL from the credit provider where the user can check the status "
            "of his or her request for credit.  This is displayed to students "
            "*after* they have requested credit."
        )
    )

    provider_description = models.TextField(
        default="",
        help_text=gettext_lazy(
            "Description for the credit provider displayed to users."
        )
    )

    fulfillment_instructions = models.TextField(  # noqa: DJ001
        null=True,
        blank=True,
        help_text=gettext_lazy(
            "Plain text or html content for displaying further steps on "
            "receipt page *after* paying for the credit to get credit for a "
            "credit course against a credit provider."
        )
    )

    eligibility_email_message = models.TextField(
        default="",
        help_text=gettext_lazy(
            "Plain text or html content for displaying custom message inside "
            "credit eligibility email content which is sent when user has met "
            "all credit eligibility requirements."
        )
    )

    receipt_email_message = models.TextField(
        default="",
        help_text=gettext_lazy(
            "Plain text or html content for displaying custom message inside "
            "credit receipt email content which is sent *after* paying to get "
            "credit for a credit course."
        )
    )

    thumbnail_url = models.URLField(
        default="",
        max_length=255,
        help_text=gettext_lazy(
            "Thumbnail image url of the credit provider."
        )
    )

    CREDIT_PROVIDERS_CACHE_KEY = "credit.providers.list"

    @classmethod
    def get_credit_providers(cls, providers_list=None):
        """
        Retrieve a list of all credit providers or filter on providers_list, represented
        as dictionaries.

        Arguments:
            provider_list (list of strings or None): contains list of ids if required results
            to be filtered, None for all providers.

        Returns:
            list of providers represented as dictionaries.

        """
        # Attempt to retrieve the credit provider list from the cache if provider_list is None
        # The cache key is invalidated when the provider list is updated
        # (a post-save signal handler on the CreditProvider model)
        # This doesn't happen very often, so we would expect a *very* high
        # cache hit rate.

        credit_providers = cache.get(cls.CREDIT_PROVIDERS_CACHE_KEY)
        if credit_providers is None:
            # Cache miss: construct the provider list and save it in the cache

            credit_providers = CreditProvider.objects.filter(active=True)

            credit_providers = [
                {
                    "id": provider.provider_id,
                    "display_name": provider.display_name,
                    "url": provider.provider_url,
                    "status_url": provider.provider_status_url,
                    "description": provider.provider_description,
                    "enable_integration": provider.enable_integration,
                    "fulfillment_instructions": provider.fulfillment_instructions,
                    "thumbnail_url": provider.thumbnail_url,
                }
                for provider in credit_providers
            ]

            cache.set(cls.CREDIT_PROVIDERS_CACHE_KEY, credit_providers)

        if providers_list:
            credit_providers = [provider for provider in credit_providers if provider['id'] in providers_list]

        return credit_providers

    @classmethod
    def get_credit_provider(cls, provider_id):
        """
        Retrieve a credit provider with provided 'provider_id'.
        """
        try:
            return CreditProvider.objects.get(active=True, provider_id=provider_id)
        except cls.DoesNotExist:
            return None

    def __str__(self):
        """Unicode representation of the credit provider. """
        return self.provider_id


@receiver(models.signals.post_save, sender=CreditProvider)
@receiver(models.signals.post_delete, sender=CreditProvider)
def invalidate_provider_cache(sender, **kwargs):  # pylint: disable=unused-argument
    """Invalidate the cache of credit providers. """
    cache.delete(CreditProvider.CREDIT_PROVIDERS_CACHE_KEY)


class CreditCourse(models.Model):
    """
    Model for tracking a credit course.

    .. no_pii:
    """

    course_key = CourseKeyField(max_length=255, db_index=True, unique=True)
    enabled = models.BooleanField(default=False)

    CREDIT_COURSES_CACHE_KEY = "credit.courses.set"

    @classmethod
    def is_credit_course(cls, course_key):
        """
        Check whether the course has been configured for credit.

        Args:
            course_key (CourseKey): Identifier of the course.

        Returns:
            bool: True iff this is a credit course.

        """
        credit_courses = cache.get(cls.CREDIT_COURSES_CACHE_KEY)
        if credit_courses is None:
            credit_courses = {
                str(course.course_key)
                for course in cls.objects.filter(enabled=True)
            }
            cache.set(cls.CREDIT_COURSES_CACHE_KEY, credit_courses)

        return str(course_key) in credit_courses

    @classmethod
    def get_credit_course(cls, course_key):
        """
        Get the credit course if exists for the given 'course_key'.

        Args:
            course_key(CourseKey): The course identifier

        Raises:
            DoesNotExist if no CreditCourse exists for the given course key.

        Returns:
            CreditCourse if one exists for the given course key.
        """
        return cls.objects.get(course_key=course_key, enabled=True)

    def __str__(self):  # noqa: DJ012
        """Unicode representation of the credit course. """
        return str(self.course_key)


@receiver(models.signals.post_save, sender=CreditCourse)
@receiver(models.signals.post_delete, sender=CreditCourse)
def invalidate_credit_courses_cache(sender, **kwargs):   # pylint: disable=unused-argument
    """Invalidate the cache of credit courses. """
    cache.delete(CreditCourse.CREDIT_COURSES_CACHE_KEY)


class CreditRequirement(TimeStampedModel):
    """
    This model represents a credit requirement.

    Each requirement is uniquely identified by its 'namespace' and
    'name' fields.
    The 'name' field stores the unique name or location (in case of XBlock)
    for a requirement, which serves as the unique identifier for that
    requirement.
    The 'display_name' field stores the display name of the requirement.
    The 'criteria' field dictionary provides additional information, clients
    may need to determine whether a user has satisfied the requirement.

    .. no_pii:
    """

    course = models.ForeignKey(CreditCourse, related_name="credit_requirements", on_delete=models.CASCADE)
    namespace = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    display_name = models.CharField(max_length=255, default="")
    criteria = JSONField()
    active = models.BooleanField(default=True)
    sort_value = models.PositiveIntegerField(default=0)

    CACHE_NAMESPACE = "credit.CreditRequirement.cache."

    class Meta:
        unique_together = ('namespace', 'name', 'course')
        ordering = ["sort_value"]

    def __str__(self):
        return f'{self.course.course_key} - {self.display_name}'

    @classmethod
    def add_or_update_course_requirement(cls, credit_course, requirement, sort_value):
        """
        Add requirement to a given course.

        Args:
            credit_course(CreditCourse): The identifier for credit course
            requirement(dict): Requirement dict to be added

        Returns:
            (CreditRequirement, created) tuple
        """

        credit_requirement, created = cls.objects.get_or_create(
            course=credit_course,
            namespace=requirement["namespace"],
            name=requirement["name"],
            defaults={
                "display_name": requirement["display_name"],
                "criteria": requirement["criteria"],
                "sort_value": sort_value,
                "active": True
            }
        )
        if not created:
            credit_requirement.criteria = requirement["criteria"]
            credit_requirement.active = True
            credit_requirement.sort_value = sort_value
            credit_requirement.display_name = requirement["display_name"]
            credit_requirement.save()

        return credit_requirement, created

    @classmethod
    @request_cached(namespace=CACHE_NAMESPACE)
    def get_course_requirements(cls, course_key, namespace=None, name=None):
        """
        Get credit requirements of a given course.

        Args:
            course_key (CourseKey): The identifier for a course

        Keyword Arguments
            namespace (str): Optionally filter credit requirements by namespace.
            name (str): Optionally filter credit requirements by name.

        Returns:
            QuerySet of CreditRequirement model

        """
        # order credit requirements according to their appearance in courseware
        requirements = CreditRequirement.objects.filter(course__course_key=course_key, active=True)

        if namespace is not None:
            requirements = requirements.filter(namespace=namespace)

        if name is not None:
            requirements = requirements.filter(name=name)

        return requirements

    @classmethod
    def disable_credit_requirements(cls, requirement_ids):
        """
        Mark the given requirements inactive.

        Args:
            requirement_ids(list): List of ids

        Returns:
            None
        """
        cls.objects.filter(id__in=requirement_ids).update(active=False)

    @classmethod
    def get_course_requirement(cls, course_key, namespace, name):
        """
        Get credit requirement of a given course.

        Args:
            course_key(CourseKey): The identifier for a course
            namespace(str): Namespace of credit course requirements
            name(str): Name of credit course requirement

        Returns:
            CreditRequirement object if exists, None otherwise.

        """
        try:
            return cls.objects.get(
                course__course_key=course_key, active=True, namespace=namespace, name=name
            )
        except cls.DoesNotExist:
            return None


@receiver(models.signals.post_save, sender=CreditRequirement)
@receiver(models.signals.post_delete, sender=CreditRequirement)
def invalidate_credit_requirement_cache(sender, **kwargs):   # pylint: disable=unused-argument
    """Invalidate the cache of credit requirements. """
    RequestCache(namespace=CreditRequirement.CACHE_NAMESPACE).clear()


class CreditRequirementStatus(TimeStampedModel):
    """
    This model represents the status of each requirement.

    For a particular credit requirement, a user can either:
    1) Have satisfied the requirement (example: approved in-course reverification)
    2) Have failed the requirement (example: denied in-course reverification)
    3) Neither satisfied nor failed (example: the user hasn't yet attempted in-course reverification).

    Cases (1) and (2) are represented by having a CreditRequirementStatus with
    the status set to "satisfied" or "failed", respectively.

    In case (3), no CreditRequirementStatus record will exist for the requirement and user.

    .. no_pii:
    """

    REQUIREMENT_STATUS_CHOICES = (
        ("satisfied", "satisfied"),
        ("failed", "failed"),
        ("declined", "declined"),
    )

    username = models.CharField(max_length=255, db_index=True)
    requirement = models.ForeignKey(CreditRequirement, related_name="statuses", on_delete=models.CASCADE)
    status = models.CharField(max_length=32, choices=REQUIREMENT_STATUS_CHOICES)

    # Include additional information about why the user satisfied or failed
    # the requirement.  This is specific to the type of requirement.
    # For example, the minimum grade requirement might record the user's
    # final grade when the user completes the course.  This allows us to display
    # the grade to users later and to send the information to credit providers.
    reason = JSONField(default={})

    class Meta:
        unique_together = ('username', 'requirement')
        verbose_name_plural = gettext_lazy('Credit requirement statuses')

    @classmethod
    def get_statuses(cls, requirements, username):
        """
        Get credit requirement statuses of given requirement and username

        Args:
            requirements(list of CreditRequirements): The identifier for a requirement
            username(str): username of the user

        Returns:
            Queryset 'CreditRequirementStatus' objects
        """
        return cls.objects.filter(requirement__in=requirements, username=username)

    @classmethod
    @transaction.atomic
    def add_or_update_requirement_status(cls, username, requirement, status="satisfied", reason=None):
        """
        Add credit requirement status for given username.

        Args:
            username(str): Username of the user
            requirement(CreditRequirement): 'CreditRequirement' object
            status(str): Status of the requirement
            reason(dict): Reason of the status

        """
        requirement_status, created = cls.objects.get_or_create(
            username=username,
            requirement=requirement,
            defaults={"reason": reason, "status": status}
        )
        if not created:
            # do not update status to `failed` if user has `satisfied` the requirement
            if status == 'failed' and requirement_status.status == 'satisfied':
                log.info(
                    'Can not change status of credit requirement "%s" from satisfied to failed ',
                    requirement_status.requirement_id
                )
                return
            requirement_status.status = status
            requirement_status.reason = reason
            requirement_status.save()

    @classmethod
    @transaction.atomic
    def remove_requirement_status(cls, username, requirement):
        """
        Remove credit requirement status for given username.

        Args:
            username(str): Username of the user
            requirement(CreditRequirement): 'CreditRequirement' object
        """

        try:
            requirement_status = cls.objects.get(username=username, requirement=requirement)
            requirement_status.delete()
        except cls.DoesNotExist:
            log_msg = (
                'The requirement status {requirement} does not exist for username {username}.'.format(  # noqa: UP032
                    requirement=requirement,
                    username=username
                )
            )
            log.error(log_msg)

    @classmethod
    def retire_user(cls, retirement):
        """
        Retire a user by anonymizing

        Args:
            retirement: UserRetirementStatus of the user being retired
        """
        requirement_statuses = cls.objects.filter(
            username=retirement.original_username
        ).update(
            username=retirement.retired_username,
            reason={},
        )
        return requirement_statuses > 0


def default_deadline_for_credit_eligibility():
    """
    The default deadline to use when creating a new CreditEligibility model.
    """
    return datetime.datetime.now(ZoneInfo("UTC")) + datetime.timedelta(
        days=getattr(settings, "CREDIT_ELIGIBILITY_EXPIRATION_DAYS", 365)
    )


class CreditEligibility(TimeStampedModel):
    """
    A record of a user's eligibility for credit for a specific course.

    .. no_pii:
    """
    username = models.CharField(max_length=255, db_index=True)
    course = models.ForeignKey(CreditCourse, related_name="eligibilities", on_delete=models.CASCADE)

    # Deadline for when credit eligibility will expire.
    # Once eligibility expires, users will no longer be able to purchase
    # or request credit.
    # We save the deadline as a database field just in case
    # we need to override the deadline for particular students.
    deadline = models.DateTimeField(
        default=default_deadline_for_credit_eligibility,
        help_text=gettext_lazy("Deadline for purchasing and requesting credit.")
    )

    class Meta:
        unique_together = ('username', 'course')
        verbose_name_plural = "Credit eligibilities"

    @classmethod
    def update_eligibility(cls, requirements, username, course_key):
        """
        Update the user's credit eligibility for a course.

        A user is eligible for credit when the user has satisfied
        all requirements for credit in the course.

        Arguments:
            requirements (Queryset): Queryset of `CreditRequirement`s to check.
            username (str): Identifier of the user being updated.
            course_key (CourseKey): Identifier of the course.

        Returns: tuple
        """
        # Check all requirements for the course to determine if the user
        # is eligible.  We need to check all the *requirements*
        # (not just the *statuses*) in case the user doesn't yet have
        # a status for a particular requirement.
        status_by_req = defaultdict(lambda: False)
        for status in CreditRequirementStatus.get_statuses(requirements, username):
            status_by_req[status.requirement.id] = status.status

        is_eligible = all(status_by_req[req.id] == "satisfied" for req in requirements)

        # If we're eligible, then mark the user as being eligible for credit.
        if is_eligible:
            try:
                CreditEligibility.objects.create(
                    username=username,
                    course=CreditCourse.objects.get(course_key=course_key),
                )
                return is_eligible, True
            except IntegrityError:
                return is_eligible, False
        else:
            return is_eligible, False

    @classmethod
    def get_user_eligibilities(cls, username):
        """
        Returns the eligibilities of given user.

        Args:
            username(str): Username of the user

        Returns:
            CreditEligibility queryset for the user

        """
        return cls.objects.filter(
            username=username,
            course__enabled=True,
            deadline__gt=datetime.datetime.now(ZoneInfo("UTC"))
        ).select_related('course')

    @classmethod
    def is_user_eligible_for_credit(cls, course_key, username):
        """
        Check if the given user is eligible for the provided credit course

        Args:
            course_key(CourseKey): The course identifier
            username(str): The username of the user

        Returns:
            Bool True if the user eligible for credit course else False
        """
        return cls.objects.filter(
            course__course_key=course_key,
            course__enabled=True,
            username=username,
            deadline__gt=datetime.datetime.now(ZoneInfo("UTC")),
        ).exists()

    def __str__(self):
        """Unicode representation of the credit eligibility. """
        return "{user}, {course}".format(  # noqa: UP032
            user=self.username,
            course=self.course.course_key,
        )


class CreditRequest(TimeStampedModel):
    """
    A request for credit from a particular credit provider.

    When a user initiates a request for credit, a CreditRequest record will be created.
    Each CreditRequest is assigned a unique identifier so we can find it when the request
    is approved by the provider.  The CreditRequest record stores the parameters to be sent
    at the time the request is made.  If the user re-issues the request
    (perhaps because the user did not finish filling in forms on the credit provider's site),
    the request record will be updated, but the UUID will remain the same.

    .. no_pii:
    """

    uuid = models.CharField(max_length=32, unique=True, db_index=True)
    username = models.CharField(max_length=255, db_index=True)
    course = models.ForeignKey(CreditCourse, related_name="credit_requests", on_delete=models.CASCADE)
    provider = models.ForeignKey(CreditProvider, related_name="credit_requests", on_delete=models.CASCADE)
    parameters = JSONField()

    REQUEST_STATUS_PENDING = "pending"
    REQUEST_STATUS_APPROVED = "approved"
    REQUEST_STATUS_REJECTED = "rejected"

    REQUEST_STATUS_CHOICES = (
        (REQUEST_STATUS_PENDING, "Pending"),
        (REQUEST_STATUS_APPROVED, "Approved"),
        (REQUEST_STATUS_REJECTED, "Rejected"),
    )
    status = models.CharField(
        max_length=255,
        choices=REQUEST_STATUS_CHOICES,
        default=REQUEST_STATUS_PENDING
    )

    class Meta:
        # Enforce the constraint that each user can have exactly one outstanding
        # request to a given provider.  Multiple requests use the same UUID.
        unique_together = ('username', 'course', 'provider')
        get_latest_by = 'created'

    @classmethod
    def retire_user(cls, retirement):
        """
        Obfuscates CreditRecord instances associated with `original_username`.
        Empties the records' `parameters` field and replaces username with its
        anonymized value, `retired_username`.
        """
        num_updated_credit_requests = cls.objects.filter(
            username=retirement.original_username
        ).update(
            username=retirement.retired_username,
            parameters={},
        )
        return num_updated_credit_requests > 0

    @classmethod
    def credit_requests_for_user(cls, username):
        """
        Retrieve all credit requests for a user.

        Arguments:
            username (unicode): The username of the user.

        Returns: list

        Example Usage:
        >>> CreditRequest.credit_requests_for_user("bob")
        [
            {
                "uuid": "557168d0f7664fe59097106c67c3f847",
                "timestamp": 1434631630,
                "course_key": "course-v1:HogwartsX+Potions101+1T2015",
                "provider": {
                    "id": "HogwartsX",
                    "display_name": "Hogwarts School of Witchcraft and Wizardry",
                },
                "status": "pending"  # or "approved" or "rejected"
            }
        ]

        """

        return [
            {
                "uuid": request.uuid,
                "timestamp": request.parameters.get("timestamp"),
                "course_key": request.course.course_key,
                "provider": {
                    "id": request.provider.provider_id,
                    "display_name": request.provider.display_name
                },
                "status": request.status
            }
            for request in cls.objects.select_related('course', 'provider').filter(username=username)
        ]

    @classmethod
    def get_user_request_status(cls, username, course_key):
        """
        Returns the latest credit request of user against the given course.

        Args:
            username(str): The username of requesting user
            course_key(CourseKey): The course identifier

        Returns:
            CreditRequest if any otherwise None

        """
        try:
            return cls.objects.filter(
                username=username, course__course_key=course_key
            ).select_related('course', 'provider').latest()
        except cls.DoesNotExist:
            return None

    def __str__(self):
        """Unicode representation of a credit request."""
        return "{course}, {provider}, {status}".format(  # noqa: UP032
            course=self.course.course_key,
            provider=self.provider.provider_id,
            status=self.status,
        )


class CreditConfig(ConfigurationModel):
    """
    Manage credit configuration

    .. no_pii:
    """
    CACHE_KEY = 'credit.providers.api.data'

    cache_ttl = models.PositiveIntegerField(
        verbose_name=gettext_lazy("Cache Time To Live"),
        default=0,
        help_text=gettext_lazy(
            "Specified in seconds. Enable caching by setting this to a value greater than 0."
        )
    )

    @property
    def is_cache_enabled(self):
        """Whether responses from the commerce API will be cached."""
        return self.enabled and self.cache_ttl > 0

    def __str__(self):
        """Unicode representation of the config. """
        return 'Credit Configuration'
