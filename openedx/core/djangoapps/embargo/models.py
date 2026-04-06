"""
Models for embargoing visits to certain courses by IP address.

WE'RE USING MIGRATIONS!

If you make changes to this model, be sure to create an appropriate migration
file and check it in at the same time as your model changes. To do that,

1. Go to the edx-platform dir
2. ./manage.py lms schemamigration embargo --auto description_of_your_change
3. Add the migration file created in edx-platform/openedx/core/djangoapps/embargo/migrations/
"""


import ipaddress
import json
import logging
from typing import List, Optional  # noqa: UP035

from config_models.models import ConfigurationModel
from django.core.cache import cache
from django.db import models
from django.db.models.signals import post_delete, post_save
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django_countries import countries
from django_countries.fields import CountryField
from opaque_keys.edx.django.models import CourseKeyField
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.xmodule_django.models import NoneToEmptyManager

from .exceptions import InvalidAccessPoint
from .messages import COURSEWARE_MESSAGES, ENROLL_MESSAGES

log = logging.getLogger(__name__)


class EmbargoedCourse(models.Model):
    """
    Enable course embargo on a course-by-course basis.

    Deprecated by `RestrictedCourse`

    .. no_pii:
    """
    objects = NoneToEmptyManager()

    # The course to embargo
    course_id = CourseKeyField(max_length=255, db_index=True, unique=True)

    # Whether or not to embargo
    embargoed = models.BooleanField(default=False)  # noqa: DJ012

    @classmethod
    def is_embargoed(cls, course_id):
        """
        Returns whether or not the given course id is embargoed.

        If course has not been explicitly embargoed, returns False.
        """
        try:
            record = cls.objects.get(course_id=course_id)
            return record.embargoed
        except cls.DoesNotExist:
            return False

    def __str__(self):  # noqa: DJ012
        not_em = "Not "
        if self.embargoed:
            not_em = ""
        return f"Course '{str(self.course_id)}' is {not_em}Embargoed"


class EmbargoedState(ConfigurationModel):
    """
    Register countries to be embargoed.

    Deprecated by `Country`.

    .. no_pii:
    """
    # The countries to embargo
    embargoed_countries = models.TextField(
        blank=True,
        help_text="A comma-separated list of country codes that fall under U.S. embargo restrictions"
    )

    @property
    def embargoed_countries_list(self):
        """
        Return a list of upper case country codes
        """
        if self.embargoed_countries == '':
            return []
        return [country.strip().upper() for country in self.embargoed_countries.split(',')]

    def __str__(self):
        return self.embargoed_countries


class RestrictedCourse(models.Model):
    """
    Course with access restrictions.

    Restricted courses can block users at two points:

    1) When enrolling in a course.
    2) When attempting to access a course the user is already enrolled in.

    The second case can occur when new restrictions
    are put into place; for example, when new countries
    are embargoed.

    Restricted courses can be configured to display
    messages to users when they are blocked.
    These displayed on pages served by the embargo app.

    .. no_pii:
    """
    COURSE_LIST_CACHE_KEY = 'embargo.restricted_courses'
    MESSAGE_URL_CACHE_KEY = 'embargo.message_url_path.{access_point}.{course_key}'

    ENROLL_MSG_KEY_CHOICES = tuple(sorted([
        (msg_key, msg.description)
        for msg_key, msg in ENROLL_MESSAGES.items()
    ]))

    COURSEWARE_MSG_KEY_CHOICES = tuple(sorted([
        (msg_key, msg.description)
        for msg_key, msg in COURSEWARE_MESSAGES.items()
    ]))

    course_key = CourseKeyField(
        max_length=255, db_index=True, unique=True,
        help_text=gettext_lazy("The course key for the restricted course.")
    )

    enroll_msg_key = models.CharField(
        max_length=255,
        choices=ENROLL_MSG_KEY_CHOICES,
        default='default',
        help_text=gettext_lazy("The message to show when a user is blocked from enrollment.")
    )

    access_msg_key = models.CharField(
        max_length=255,
        choices=COURSEWARE_MSG_KEY_CHOICES,
        default='default',
        help_text=gettext_lazy("The message to show when a user is blocked from accessing a course.")
    )

    disable_access_check = models.BooleanField(
        default=False,
        help_text=gettext_lazy(
            "Allow users who enrolled in an allowed country "
            "to access restricted courses from excluded countries."
        )
    )

    @classmethod
    def is_restricted_course(cls, course_key: CourseKey) -> bool:
        """
        Check if the course is in restricted list

        Args:
            course_key: course to look for

        Returns:
            True if the course is in the restricted course list.
        """
        return str(course_key) in cls._get_restricted_courses_from_cache()

    @classmethod
    def is_disabled_access_check(cls, course_key: CourseKey) -> bool:
        """
        Check if the course is in restricted list has disabled_access_check

        Args:
            course_key: course to look for

        Returns:
            disabled_access_check attribute of restricted course
        """

        # checking is_restricted_course method also here to make sure course exists in the list otherwise in case of
        # no course found it will throw the key not found error on 'disable_access_check'
        return (
            cls.is_restricted_course(course_key)
            and cls._get_restricted_courses_from_cache().get(str(course_key))["disable_access_check"]
        )

    @classmethod
    def _get_restricted_courses_from_cache(cls):
        """
        Cache all restricted courses and returns the dict of course_keys and disable_access_check that are restricted
        """
        restricted_courses = cache.get(cls.COURSE_LIST_CACHE_KEY)
        if restricted_courses is None:
            restricted_courses = {
                str(course.course_key): {
                    'disable_access_check': course.disable_access_check
                }
                for course in RestrictedCourse.objects.all()
            }
            cache.set(cls.COURSE_LIST_CACHE_KEY, restricted_courses)
        return restricted_courses

    def snapshot(self):
        """Return a snapshot of all access rules for this course.

        This is useful for recording an audit trail of rule changes.
        The returned dictionary is JSON-serializable.

        Returns:
            dict

        Example Usage:
        >>> restricted_course.snapshot()
        {
            'enroll_msg': 'default',
            'access_msg': 'default',
            'country_rules': [
                {'country': 'IR', 'rule_type': 'blacklist'},
                {'country': 'CU', 'rule_type': 'blacklist'}
            ]
        }

        """
        country_rules_for_course = (
            CountryAccessRule.objects
        ).select_related('country').filter(restricted_course=self)

        return {
            'enroll_msg': self.enroll_msg_key,
            'access_msg': self.access_msg_key,
            'country_rules': [
                {
                    'country': str(rule.country.country),
                    'rule_type': rule.rule_type
                }
                for rule in country_rules_for_course
            ]
        }

    def message_key_for_access_point(self, access_point: str) -> Optional[str]:  # noqa: UP045
        """Determine which message to show the user.

        The message can be configured per-course and depends
        on how the user is trying to access the course
        (trying to enroll or accessing courseware).

        Arguments:
            access_point: Either "courseware" or "enrollment"

        Returns:
            The message key. If the access point is not valid, returns None instead.

        """
        if access_point == 'enrollment':
            return self.enroll_msg_key
        elif access_point == 'courseware':
            return self.access_msg_key

    def __str__(self):  # noqa: DJ012
        return str(self.course_key)

    @classmethod
    def message_url_path(cls, course_key: CourseKey, access_point: str) -> str:
        """Determine the URL path for the message explaining why the user was blocked.

        This is configured per-course.  See `RestrictedCourse` in the `embargo.models`
        module for more details.

        Arguments:
            course_key: The location of the course.
            access_point: How the user was trying to access the course. Can be either "enrollment" or "courseware".

        Returns:
            The URL path to a page explaining why the user was blocked.

        Raises:
            InvalidAccessPoint: Raised if access_point is not a supported value.

        """
        if access_point not in ['enrollment', 'courseware']:
            raise InvalidAccessPoint(access_point)

        # First check the cache to see if we already have
        # a URL for this (course_key, access_point) tuple
        cache_key = cls.MESSAGE_URL_CACHE_KEY.format(
            access_point=access_point,
            course_key=course_key
        )
        url = cache.get(cache_key)

        # If there's a cache miss, we'll need to retrieve the message
        # configuration from the database
        if url is None:
            url = cls._get_message_url_path_from_db(course_key, access_point)
            cache.set(cache_key, url)

        return url

    @classmethod
    def _get_message_url_path_from_db(cls, course_key: CourseKey, access_point: str) -> str:
        """Retrieve the "blocked" message from the database.

        Arguments:
            course_key: The location of the course.
            access_point: How the user was trying to access the course. Can be either "enrollment" or "courseware".

        Returns:
            The URL path to a page explaining why the user was blocked.

        """
        # Fallback in case we're not able to find a message path
        # Presumably if the caller is requesting a URL, the caller
        # has already determined that the user should be blocked.
        # We use generic messaging unless we find something more specific,
        # but *always* return a valid URL path.
        default_path = reverse(
            'embargo:blocked_message',
            kwargs={
                'access_point': 'courseware',
                'message_key': 'default'
            }
        )

        # First check whether this is a restricted course.
        # The list of restricted courses is cached, so this does
        # not require a database query.
        if not cls.is_restricted_course(course_key):
            return default_path

        # Retrieve the message key from the restricted course
        # for this access point, then determine the URL.
        try:
            course = cls.objects.get(course_key=course_key)
            msg_key = course.message_key_for_access_point(access_point)
            return reverse(
                'embargo:blocked_message',
                kwargs={
                    'access_point': access_point,
                    'message_key': msg_key
                }
            )
        except cls.DoesNotExist:
            # This occurs only if there's a race condition
            # between cache invalidation and database access.
            return default_path

    @classmethod
    def invalidate_cache_for_course(cls, course_key: CourseKey) -> None:
        """Invalidate the caches for the restricted course. """
        cache.delete(cls.COURSE_LIST_CACHE_KEY)
        log.info("Invalidated cached list of restricted courses.")

        for access_point in ['enrollment', 'courseware']:
            msg_cache_key = cls.MESSAGE_URL_CACHE_KEY.format(
                access_point=access_point,
                course_key=course_key
            )
            cache.delete(msg_cache_key)
        log.info("Invalidated cached messaging URLs ")


class Country(models.Model):
    """Representation of a country.

    This is used to define country-based access rules.
    There is a data migration that creates entries for
    each country code.

    .. no_pii:
    """
    country = CountryField(
        db_index=True, unique=True,
        help_text=gettext_lazy("Two character ISO country code.")
    )

    def __str__(self):
        return "{name} ({code})".format(  # noqa: UP032
            name=str(self.country.name),
            code=str(self.country)
        )

    class Meta:  # noqa: DJ012
        """Default ordering is ascending by country code """
        ordering = ['country']


class CountryAccessRule(models.Model):
    """Course access rule based on the user's country.

    The rule applies to a particular course-country pair.
    Countries can either be whitelisted or blacklisted,
    but not both.

    To determine whether a user has access to a course
    based on the user's country:

    1) Retrieve the list of whitelisted countries for the course.
    (If there aren't any, then include every possible country.)

    2) From the initial list, remove all blacklisted countries
    for the course.

    .. no_pii:
    """

    WHITELIST_RULE = 'whitelist'
    BLACKLIST_RULE = 'blacklist'

    RULE_TYPE_CHOICES = (
        (WHITELIST_RULE, 'Whitelist (allow only these countries)'),
        (BLACKLIST_RULE, 'Blacklist (block these countries)'),
    )

    rule_type = models.CharField(
        max_length=255,
        choices=RULE_TYPE_CHOICES,
        default=BLACKLIST_RULE,
        help_text=gettext_lazy(
            "Whether to include or exclude the given course. "
            "If whitelist countries are specified, then ONLY users from whitelisted countries "
            "will be able to access the course.  If blacklist countries are specified, then "
            "users from blacklisted countries will NOT be able to access the course."
        )
    )

    restricted_course = models.ForeignKey(
        "RestrictedCourse",
        help_text=gettext_lazy("The course to which this rule applies."),
        on_delete=models.CASCADE,
    )

    country = models.ForeignKey(
        "Country",
        help_text=gettext_lazy("The country to which this rule applies."),
        on_delete=models.CASCADE,
    )

    CACHE_KEY = "embargo.allowed_countries.{course_key}"

    ALL_COUNTRIES = {code[0] for code in list(countries)}

    @classmethod
    def check_country_access(cls, course_key: CourseKey, country: str) -> bool:
        """
        Check if the country is either in whitelist or blacklist of countries for the course_id

        Args:
            course_key: course to look for
            country: A 2 characters code of country

        Returns:
            True if country found in allowed country, otherwise check given country exists in list
        """
        # If the country code is not in the list of all countries,
        # we don't want to automatically exclude the user.
        # This can happen, for example, when GeoIP falls back
        # to using a continent code because it cannot determine
        # the specific country.
        if country not in cls.ALL_COUNTRIES:
            return True

        cache_key = cls.CACHE_KEY.format(course_key=course_key)
        allowed_countries = cache.get(cache_key)
        if allowed_countries is None:
            allowed_countries = cls._get_country_access_list(course_key)
            cache.set(cache_key, allowed_countries)

        return country == '' or country in allowed_countries

    @classmethod
    def _get_country_access_list(cls, course_key: CourseKey) -> List[str]:  # noqa: UP006
        """
        if a course is blacklist for two countries then course can be accessible from
        any where except these two countries.
        if a course is whitelist for two countries then course can be accessible from
        these countries only.
        Args:
            course_key: course to look for
        Returns:
            Consolidated list of accessible countries for given course
        """

        whitelist_countries = set()
        blacklist_countries = set()

        # Retrieve all rules in one database query, performing the "join" with the Country table
        rules_for_course = CountryAccessRule.objects.select_related('country').filter(
            restricted_course__course_key=course_key
        )

        # Filter the rules into a whitelist and blacklist in one pass
        for rule in rules_for_course:
            if rule.rule_type == cls.WHITELIST_RULE:
                whitelist_countries.add(rule.country.country.code)
            elif rule.rule_type == cls.BLACKLIST_RULE:
                blacklist_countries.add(rule.country.country.code)

        # If there are no whitelist countries, default to all countries
        if not whitelist_countries:
            whitelist_countries = cls.ALL_COUNTRIES

        # Consolidate the rules into a single list of countries
        # that have access to the course.
        return list(whitelist_countries - blacklist_countries)

    def __str__(self):  # noqa: DJ012
        if self.rule_type == self.WHITELIST_RULE:
            return _("Whitelist {country} for {course}").format(
                course=str(self.restricted_course.course_key),
                country=str(self.country),
            )
        elif self.rule_type == self.BLACKLIST_RULE:
            return _("Blacklist {country} for {course}").format(
                course=str(self.restricted_course.course_key),
                country=str(self.country),
            )

    @classmethod
    def invalidate_cache_for_course(cls, course_key: CourseKey) -> None:
        """Invalidate the cache. """
        cache_key = cls.CACHE_KEY.format(course_key=course_key)
        cache.delete(cache_key)
        log.info("Invalidated country access list for course %s", course_key)

    class Meta:  # noqa: DJ012
        """a course can be added with either black or white list.  """
        unique_together = (
            # This restriction ensures that a country is on
            # either the whitelist or the blacklist, but
            # not both (for a particular course).
            ("restricted_course", "country")
        )


def invalidate_country_rule_cache(sender, instance, **kwargs):  # pylint: disable=unused-argument
    """Invalidate cached rule information on changes to the rule models.

    We need to handle this in a Django receiver, because Django admin
    doesn't always call the model's `delete()` method directly during
    a bulk delete operation.

    Arguments:
        sender: Not used, but required by Django receivers.
        instance (RestrictedCourse or CountryAccessRule): The instance
            being saved or deleted.

    """
    if isinstance(instance, RestrictedCourse):
        # If a restricted course changed, we need to update the list
        # of which courses are restricted as well as any rules
        # associated with the course.
        RestrictedCourse.invalidate_cache_for_course(instance.course_key)
        CountryAccessRule.invalidate_cache_for_course(instance.course_key)
    if isinstance(instance, CountryAccessRule):
        try:
            restricted_course = instance.restricted_course
        except RestrictedCourse.DoesNotExist:
            # If the restricted course and its rules are being deleted,
            # the restricted course may not exist at this point.
            # However, the cache should have been invalidated
            # when the restricted course was deleted.
            pass
        else:
            # Invalidate the cache of countries for the course.
            CountryAccessRule.invalidate_cache_for_course(restricted_course.course_key)


# Hook up the cache invalidation receivers to the appropriate
# post_save and post_delete signals.
post_save.connect(invalidate_country_rule_cache, sender=CountryAccessRule)
post_save.connect(invalidate_country_rule_cache, sender=RestrictedCourse)
post_delete.connect(invalidate_country_rule_cache, sender=CountryAccessRule)
post_delete.connect(invalidate_country_rule_cache, sender=RestrictedCourse)


class CourseAccessRuleHistory(models.Model):  # noqa: DJ008
    """
    History of course access rule changes.

    .. no_pii:
    """
    # pylint: disable=model-missing-unicode

    timestamp = models.DateTimeField(db_index=True, auto_now_add=True)
    course_key = CourseKeyField(max_length=255, db_index=True)
    snapshot = models.TextField(null=True, blank=True)  # noqa: DJ001

    DELETED_PLACEHOLDER = "DELETED"

    @classmethod
    def save_snapshot(cls, restricted_course, deleted=False):
        """Save a snapshot of access rules for a course.

        Arguments:
            restricted_course (RestrictedCourse)

        Keyword Arguments:
            deleted (boolean): If True, the restricted course
                is about to be deleted.  Create a placeholder
                snapshot recording that the course and all its
                rules was deleted.

        Returns:
            None

        """
        course_key = restricted_course.course_key

        # At the point this is called, the access rules may not have
        # been deleted yet.  When the rules *are* deleted, the
        # restricted course entry may no longer exist, so we
        # won't be able to take a snapshot of the rules.
        # To handle this, we save a placeholder "DELETED" entry
        # so that it's clear in the audit that the restricted
        # course (along with all its rules) was deleted.
        snapshot = (
            CourseAccessRuleHistory.DELETED_PLACEHOLDER if deleted
            else json.dumps(restricted_course.snapshot())
        )

        cls.objects.create(
            course_key=course_key,
            snapshot=snapshot
        )

    @staticmethod
    def snapshot_post_save_receiver(sender, instance, **kwargs):  # pylint: disable=unused-argument
        """Create a snapshot of course access rules when the rules are updated. """
        if isinstance(instance, RestrictedCourse):
            CourseAccessRuleHistory.save_snapshot(instance)
        elif isinstance(instance, CountryAccessRule):
            CourseAccessRuleHistory.save_snapshot(instance.restricted_course)

    @staticmethod
    def snapshot_post_delete_receiver(sender, instance, **kwargs):  # pylint: disable=unused-argument
        """Create a snapshot of course access rules when rules are deleted. """
        if isinstance(instance, RestrictedCourse):
            CourseAccessRuleHistory.save_snapshot(instance, deleted=True)
        elif isinstance(instance, CountryAccessRule):
            try:
                restricted_course = instance.restricted_course
            except RestrictedCourse.DoesNotExist:
                # When Django admin deletes a restricted course, it will
                # also delete the rules associated with that course.
                # At this point, we can't access the restricted course
                # from the rule beause it may already have been deleted.
                # If this happens, we don't need to record anything,
                # since we already record a placeholder "DELETED"
                # entry when the restricted course record is deleted.
                pass
            else:
                CourseAccessRuleHistory.save_snapshot(restricted_course)

    class Meta:  # noqa: DJ012
        get_latest_by = 'timestamp'


class GlobalRestrictedCountry(models.Model):
    """
    Model to restrict access to specific countries globally.
    """
    country = models.ForeignKey(
        "Country",
        help_text="The country to be restricted.",
        on_delete=models.CASCADE,
        unique=True
    )

    CACHE_KEY = "embargo.global.restricted_countries"

    @classmethod
    def get_countries(cls):
        """
        Retrieve the set of restricted country codes from the cache or refresh it if not available.

        Returns:
            set: A set of restricted country codes.
        """
        return cache.get_or_set(cls.CACHE_KEY, cls._fetch_restricted_countries)

    @classmethod
    def is_country_restricted(cls, country_code):
        """
        Check if the given country code is restricted.

        Args:
            country_code (str): The country code to check.

        Returns:
            bool: True if the country is restricted, False otherwise.
        """
        return country_code in cls.get_countries()

    @classmethod
    def _fetch_restricted_countries(cls):
        """
        Fetch the set of restricted country codes from the database.

        Returns:
            set: A set of restricted country codes.
        """
        return set(cls.objects.values_list("country__country", flat=True))

    @classmethod
    def update_cache(cls):
        """
        Update the cache with the latest restricted country codes.
        """
        cache.set(cls.CACHE_KEY, cls._fetch_restricted_countries())

    def save(self, *args, **kwargs):  # noqa: DJ012
        """
        Override save method to update cache on insert/update.
        """
        super().save(*args, **kwargs)
        self.update_cache()

    def delete(self, *args, **kwargs):
        """
        Override delete method to update cache on deletion.
        """
        super().delete(*args, **kwargs)
        self.update_cache()

    def __str__(self):  # noqa: DJ012
        return f"{self.country.country.name} ({self.country.country})"

    class Meta:  # noqa: DJ012
        verbose_name = "Global Restricted Country"
        verbose_name_plural = "Global Restricted Countries"


# Connect the signals to the receivers so we record a history
# of changes to the course access rules.
post_save.connect(CourseAccessRuleHistory.snapshot_post_save_receiver, sender=RestrictedCourse)
post_save.connect(CourseAccessRuleHistory.snapshot_post_save_receiver, sender=CountryAccessRule)
post_delete.connect(CourseAccessRuleHistory.snapshot_post_delete_receiver, sender=RestrictedCourse)
post_delete.connect(CourseAccessRuleHistory.snapshot_post_delete_receiver, sender=CountryAccessRule)


class IPFilter(ConfigurationModel):
    """
    Register specific IP addresses to explicitly block or unblock.

    .. no_pii:
    """
    whitelist = models.TextField(
        blank=True,
        help_text="A comma-separated list of IP addresses that should not fall under embargo restrictions."
    )

    blacklist = models.TextField(
        blank=True,
        help_text="A comma-separated list of IP addresses that should fall under embargo restrictions."
    )

    class IPFilterList:
        """
        Represent a list of IP addresses with support of networks.
        """

        def __init__(self, ips):
            self.networks = [ipaddress.ip_network(ip) for ip in ips]

        def __iter__(self):
            yield from self.networks

        def __contains__(self, ip_addr):
            try:
                ip_addr = ipaddress.ip_address(ip_addr)
            except ValueError:
                return False

            for network in self.networks:
                if ip_addr in network:
                    return True

            return False

    @property
    def whitelist_ips(self):
        """
        Return a list of valid IP addresses to whitelist
        """
        if self.whitelist == '':
            return []
        return self.IPFilterList([addr.strip() for addr in self.whitelist.split(',')])

    @property
    def blacklist_ips(self):
        """
        Return a list of valid IP addresses to blacklist
        """
        if self.blacklist == '':
            return []
        return self.IPFilterList([addr.strip() for addr in self.blacklist.split(',')])

    def __str__(self):
        return f"Whitelist: {self.whitelist_ips} - Blacklist: {self.blacklist_ips}"
