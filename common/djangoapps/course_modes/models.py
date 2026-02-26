"""
Add and create new modes for running courses on this particular LMS
"""


from collections import defaultdict, namedtuple
from datetime import timedelta
import logging
from config_models.models import ConfigurationModel
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_comma_separated_integer_list
from django.db import models
from django.db.models import Q
from django.dispatch import receiver

from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from edx_django_utils.cache import RequestCache
from opaque_keys.edx.django.models import CourseKeyField
from simple_history.models import HistoricalRecords

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.lib.cache_utils import request_cached

log = logging.getLogger(__name__)

Mode = namedtuple('Mode',
                  [
                      'slug',
                      'name',
                      'min_price',
                      'suggested_prices',
                      'currency',
                      'expiration_datetime',
                      'description',
                      'sku',
                      'android_sku',
                      'ios_sku',
                      'bulk_sku',
                  ])


class CourseMode(models.Model):
    """
    We would like to offer a course in a variety of modes.

    .. no_pii:
    """
    course = models.ForeignKey(
        CourseOverview,
        db_constraint=False,
        db_index=True,
        related_name='modes',
        on_delete=models.DO_NOTHING,
    )

    # the reference to this mode that can be used by Enrollments to generate
    # similar behavior for the same slug across courses
    mode_slug = models.CharField(max_length=100, verbose_name=_("Mode"))

    # The 'pretty' name that can be translated and displayed
    mode_display_name = models.CharField(max_length=255, verbose_name=_("Display Name"))

    # The price in USD that we would like to charge for this mode of the course
    # Historical note: We used to allow users to choose from several prices, but later
    # switched to using a single price.  Although this field is called `min_price`, it is
    # really just the price of the course.
    min_price = models.IntegerField(default=0, verbose_name=_("Price"))

    # the currency these prices are in, using lower case ISO currency codes
    currency = models.CharField(default="usd", max_length=8)

    # The datetime at which the course mode will expire.
    # This is used to implement "upgrade" deadlines.
    # For example, if there is a verified mode that expires on 1/1/2015,
    # then users will be able to upgrade into the verified mode before that date.
    # Once the date passes, users will no longer be able to enroll as verified.
    _expiration_datetime = models.DateTimeField(
        default=None, null=True, blank=True,
        verbose_name=_("Upgrade Deadline"),
        help_text=_(
            "OPTIONAL: After this date/time, users will no longer be able to enroll in this mode. "
            "Leave this blank if users can enroll in this mode until enrollment closes for the course."
        ),
        db_column='expiration_datetime',
    )

    # The system prefers to set this automatically based on default settings. But
    # if the field is set manually we want a way to indicate that so we don't
    # overwrite the manual setting of the field.
    expiration_datetime_is_explicit = models.BooleanField(
        default=False,
        verbose_name=_("Lock upgrade deadline date"),
        help_text=_(
            "OPTIONAL: Set to True to lock in the explicitly defined upgrade deadline date. "
            "Set to False if there is no upgrade deadline or to use the default upgrade deadline."
        )
    )

    # DEPRECATED: the `expiration_date` field has been replaced by `expiration_datetime`
    expiration_date = models.DateField(default=None, null=True, blank=True)

    # DEPRECATED: the suggested prices for this mode
    # We used to allow users to choose from a set of prices, but we now allow only
    # a single price.  This field has been deprecated by `min_price`
    suggested_prices = models.CharField(max_length=255, blank=True, default='',
                                        validators=[validate_comma_separated_integer_list])

    # optional description override
    # WARNING: will not be localized
    description = models.TextField(null=True, blank=True)

    # Optional SKU for integration with the ecommerce service
    sku = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        verbose_name="SKU",
        help_text=_(
            "OPTIONAL: This is the SKU (stock keeping unit) of this mode in the external ecommerce service.  "
            "Leave this blank if the course has not yet been migrated to the ecommerce service."
        )
    )

    # Optional Android SKU for integration with mobile and the ecommerce service
    android_sku = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        verbose_name="Android SKU",
        help_text=_(
            "OPTIONAL: This is the Android SKU registered on play store for this mode of the course. "
            "Leave this blank if the course has not yet been migrated to the ecommerce service."
        )
    )

    # Optional iOS SKU for integration with mobile and the ecommerce service
    ios_sku = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        verbose_name="iOS SKU",
        help_text=_(
            "OPTIONAL: This is the iOS SKU registered on app store for this mode of the course.  "
            "Leave this blank if the course has not yet been migrated to the ecommerce service."
        )
    )

    # Optional bulk order SKU for integration with the ecommerce service
    bulk_sku = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        default=None,  # Need this in order to set DEFAULT NULL on the database column
        verbose_name="Bulk SKU",
        help_text=_(
            "This is the bulk SKU (stock keeping unit) of this mode in the external ecommerce service."
        )
    )

    history = HistoricalRecords()

    HONOR = 'honor'
    PROFESSIONAL = 'professional'
    VERIFIED = 'verified'
    AUDIT = 'audit'
    NO_ID_PROFESSIONAL_MODE = 'no-id-professional'
    CREDIT_MODE = 'credit'
    MASTERS = 'masters'
    EXECUTIVE_EDUCATION = 'executive-education'
    PAID_EXECUTIVE_EDUCATION = 'paid-executive-education'
    UNPAID_EXECUTIVE_EDUCATION = 'unpaid-executive-education'
    PAID_BOOTCAMP = 'paid-bootcamp'
    UNPAID_BOOTCAMP = 'unpaid-bootcamp'

    DEFAULT_MODE = Mode(
        settings.COURSE_MODE_DEFAULTS['slug'],
        settings.COURSE_MODE_DEFAULTS['name'],
        settings.COURSE_MODE_DEFAULTS['min_price'],
        settings.COURSE_MODE_DEFAULTS['suggested_prices'],
        settings.COURSE_MODE_DEFAULTS['currency'],
        settings.COURSE_MODE_DEFAULTS['expiration_datetime'],
        settings.COURSE_MODE_DEFAULTS['description'],
        settings.COURSE_MODE_DEFAULTS['sku'],
        settings.COURSE_MODE_DEFAULTS['android_sku'],
        settings.COURSE_MODE_DEFAULTS['ios_sku'],
        settings.COURSE_MODE_DEFAULTS['bulk_sku'],
    )
    DEFAULT_MODE_SLUG = settings.COURSE_MODE_DEFAULTS['slug']

    ALL_MODES = [
        AUDIT,
        CREDIT_MODE,
        HONOR,
        NO_ID_PROFESSIONAL_MODE,
        PROFESSIONAL,
        VERIFIED,
        MASTERS,
        EXECUTIVE_EDUCATION,
        PAID_EXECUTIVE_EDUCATION,
        UNPAID_EXECUTIVE_EDUCATION,
        PAID_BOOTCAMP,
        UNPAID_BOOTCAMP
    ]

    # Modes utilized for audit/free enrollments
    AUDIT_MODES = [AUDIT, HONOR, UNPAID_EXECUTIVE_EDUCATION, UNPAID_BOOTCAMP]

    # Modes that allow a student to pursue a verified certificate
    VERIFIED_MODES = [
        VERIFIED, PROFESSIONAL, MASTERS, EXECUTIVE_EDUCATION, PAID_EXECUTIVE_EDUCATION, PAID_BOOTCAMP
    ]

    # Modes that allow a student to pursue a non-verified certificate
    NON_VERIFIED_MODES = [HONOR, AUDIT, NO_ID_PROFESSIONAL_MODE]

    # Modes that allow a student to earn credit with a university partner
    CREDIT_MODES = [CREDIT_MODE]

    # Modes that are eligible to purchase credit
    CREDIT_ELIGIBLE_MODES = [
        VERIFIED, PROFESSIONAL, NO_ID_PROFESSIONAL_MODE, EXECUTIVE_EDUCATION, PAID_EXECUTIVE_EDUCATION,
        PAID_BOOTCAMP
    ]

    # Modes for which certificates/programs may need to be updated
    CERTIFICATE_RELEVANT_MODES = CREDIT_MODES + CREDIT_ELIGIBLE_MODES + [MASTERS]

    # Modes that are allowed to upsell
    UPSELL_TO_VERIFIED_MODES = [HONOR, AUDIT]

    CACHE_NAMESPACE = "course_modes.CourseMode.cache."

    class Meta:
        app_label = "course_modes"
        unique_together = ('course', 'mode_slug', 'currency')

    def __init__(self, *args, **kwargs):  # lint-amnesty, pylint: disable=useless-super-delegation
        super().__init__(*args, **kwargs)

    def clean(self):
        """
        Object-level validation - implemented in this method so DRF serializers
        catch errors in advance of a save() attempt.
        """
        if self.is_professional_slug(self.mode_slug) and self.expiration_datetime is not None:
            raise ValidationError(
                _("Professional education modes are not allowed to have expiration_datetime set.")
            )

        mode_config = settings.COURSE_ENROLLMENT_MODES.get(self.mode_slug, {})
        min_price_for_mode = mode_config.get('min_price', 0)
        if int(self.min_price) < min_price_for_mode:
            mode_display_name = mode_config.get('display_name', self.mode_slug)
            raise ValidationError(
                _(  # lint-amnesty, pylint: disable=translation-of-non-string
                    "The {course_mode} course mode has a minimum price of {min_price}. You must set a price greater than or equal to {min_price}.".format(  # lint-amnesty, pylint: disable=line-too-long
                        course_mode=mode_display_name, min_price=min_price_for_mode
                    )
                )
            )

    def save(self, force_insert=False, force_update=False, using=None):  # lint-amnesty, pylint: disable=arguments-differ
        # Ensure currency is always lowercase.
        self.clean()  # ensure object-level validation is performed before we save.
        self.currency = self.currency.lower()
        if self.id is None:
            # If this model has no primary key at save time, it needs to be force-inserted.
            force_insert = True
        super().save(force_insert, force_update, using)

    @property
    def slug(self):
        """
        Returns mode_slug

        NOTE (CCB): This is a silly hack needed because all of the class methods use tuples
        with a property named slug instead of mode_slug.
        """
        return self.mode_slug

    @property
    def expiration_datetime(self):
        """ Return _expiration_datetime. """
        return self._expiration_datetime

    @expiration_datetime.setter
    def expiration_datetime(self, new_datetime):
        """ Saves datetime to _expiration_datetime and sets the explicit flag. """
        # Only set explicit flag if we are setting an actual date.
        if new_datetime is not None:
            self.expiration_datetime_is_explicit = True
        self._expiration_datetime = new_datetime

    @classmethod
    def get_default_mode_slug(cls):
        """
        Returns the default mode slug to be used in the CourseEnrollment model mode field
        as the default value.
        """
        return cls.DEFAULT_MODE_SLUG

    @classmethod
    def all_modes_for_courses(cls, course_id_list):
        """Find all modes for a list of course IDs, including expired modes.

        Courses that do not have a course mode will be given a default mode.

        Arguments:
            course_id_list (list): List of `CourseKey`s

        Returns:
            dict mapping `CourseKey` to lists of `Mode`

        """
        modes_by_course = defaultdict(list)
        for mode in cls.objects.filter(course_id__in=course_id_list):
            modes_by_course[mode.course_id].append(mode.to_tuple())

        # Assign default modes if nothing available in the database
        missing_courses = set(course_id_list) - set(modes_by_course.keys())
        for course_id in missing_courses:
            modes_by_course[course_id] = [cls.DEFAULT_MODE]

        return modes_by_course

    @classmethod
    def all_and_unexpired_modes_for_courses(cls, course_id_list):
        """Retrieve course modes for a list of courses.

        To reduce the number of database queries, this function
        loads *all* course modes, then creates a second list
        of unexpired course modes.

        Arguments:
            course_id_list (list of `CourseKey`): List of courses for which
                to retrieve course modes.

        Returns:
            Tuple of `(all_course_modes, unexpired_course_modes)`, where
            the first is a list of *all* `Mode`s (including expired ones),
            and the second is a list of only unexpired `Mode`s.

        """
        now_dt = now()
        all_modes = cls.all_modes_for_courses(course_id_list)
        unexpired_modes = {
            course_id: [
                mode for mode in modes
                if mode.expiration_datetime is None or mode.expiration_datetime >= now_dt
            ]
            for course_id, modes in all_modes.items()
        }

        return (all_modes, unexpired_modes)

    @classmethod
    def paid_modes_for_course(cls, course_id):
        """
        Returns a list of non-expired modes for a course ID that have a set minimum price.

        If no modes have been set, returns an empty list.

        Args:
            course_id (CourseKey): The course to find paid modes for.

        Returns:
            A list of CourseModes with a minimum price.

        """
        found_course_modes = cls.objects.filter(
            Q(course_id=course_id) &
            Q(min_price__gt=0) &
            (
                Q(_expiration_datetime__isnull=True) |
                Q(_expiration_datetime__gte=now())
            )
        )
        return [mode.to_tuple() for mode in found_course_modes]

    @classmethod
    @request_cached(CACHE_NAMESPACE)
    def modes_for_course(
        cls, course_id=None, include_expired=False, only_selectable=True, course=None,
    ):
        """
        Returns a list of the non-expired modes for a given course id

        If no modes have been set in the table, returns the default mode

        Keyword Arguments:
            course_id (CourseKey): Search for course modes for this course.

            include_expired (bool): If True, expired course modes will be included
            in the returned JSON data. If False, these modes will be omitted.

            only_selectable (bool): If True, include only modes that are shown
                to users on the track selection page.  (Currently, "credit" modes
                aren't available to users until they complete the course, so
                they are hidden in track selection.)

            course (CourseOverview): The course to select course modes from.

        Returns:
            list of `Mode` tuples

        """
        if course_id is None and course is None:
            raise ValueError("One of course_id or course must not be None.")

        if course is not None and not isinstance(course, CourseOverview):
            # CourseBlocks don't have the data needed to pull related modes,
            # so we'll fall back on course_id-based lookup instead
            course_id = course.id
            course = None

        if course is not None:
            found_course_modes = course.modes.all()
        else:
            found_course_modes = cls.objects.filter(course_id=course_id)

        # Filter out expired course modes if include_expired is not set
        if not include_expired:
            found_course_modes = found_course_modes.filter(
                Q(_expiration_datetime__isnull=True) | Q(_expiration_datetime__gte=now())
            )

        # Credit course modes are currently not shown on the track selection page;
        # they're available only when students complete a course.  For this reason,
        # we exclude them from the list if we're only looking for selectable modes
        # (e.g. on the track selection page or in the payment/verification flows).
        if only_selectable:
            found_course_modes = found_course_modes.exclude(mode_slug__in=cls.CREDIT_MODES)

        modes = ([mode.to_tuple() for mode in found_course_modes])
        if not modes:
            modes = [cls.DEFAULT_MODE]

        return modes

    @classmethod
    def modes_for_course_dict(cls, course_id=None, modes=None, **kwargs):
        """Returns the non-expired modes for a particular course.

        Arguments:
            course_id (CourseKey): Search for course modes for this course.

        Keyword Arguments:
            modes (list of `Mode`): If provided, search through this list
                of course modes.  This can be used to avoid an additional
                database query if you have already loaded the modes list.

            include_expired (bool): If True, expired course modes will be included
                in the returned values. If False, these modes will be omitted.

            only_selectable (bool): If True, include only modes that are shown
                to users on the track selection page.  (Currently, "credit" modes
                aren't available to users until they complete the course, so
                they are hidden in track selection.)

        Returns:
            dict: Keys are mode slugs, values are lists of `Mode` namedtuples.

        """
        if modes is None:
            modes = cls.modes_for_course(course_id, **kwargs)

        return {mode.slug: mode for mode in modes}

    @classmethod
    def mode_for_course(cls, course_id, mode_slug, modes=None, include_expired=False):
        """Returns the mode for the course corresponding to mode_slug.

        Returns only non-expired modes.

        If this particular mode is not set for the course, returns None

        Arguments:
            course_id (CourseKey): Search for course modes for this course.
            mode_slug (str): Search for modes with this slug.

        Keyword Arguments:
            modes (list of `Mode`): If provided, search through this list
                of course modes.  This can be used to avoid an additional
                database query if you have already loaded the modes list.

            include_expired (bool): If True, expired course modes will be included
                in the returned values. If False, these modes will be omitted.

        Returns:
            Mode

        """
        if modes is None:
            modes = cls.modes_for_course(course_id, include_expired=include_expired)

        matched = [m for m in modes if m.slug == mode_slug]
        if matched:
            return matched[0]
        else:
            return None

    @classmethod
    def verified_mode_for_course(cls, course_id=None, modes=None, include_expired=False, course=None):
        """Find a verified mode for a particular course.

        Since we have multiple modes that can go through the verify flow,
        we want to be able to select the 'correct' verified mode for a given course.

        Currently, we prefer to return the professional mode over the verified one
        if both exist for the given course.

        Arguments:
            course_id (CourseKey): Search for course modes for this course.

        Keyword Arguments:
            modes (list of `Mode`): If provided, search through this list
                of course modes.  This can be used to avoid an additional
                database query if you have already loaded the modes list.

        Returns:
            Mode or None

        """
        modes_dict = cls.modes_for_course_dict(
            course_id=course_id,
            modes=modes,
            include_expired=include_expired,
            course=course
        )
        verified_mode = modes_dict.get('verified', None)
        professional_mode = modes_dict.get('professional', None)
        # we prefer professional over verify
        return professional_mode if professional_mode else verified_mode

    @classmethod
    def min_course_price_for_verified_for_currency(cls, course_id, currency):  # pylint: disable=invalid-name
        """
        Returns the minimum price of the course in the appropriate currency over all the
        course's *verified*, non-expired modes.

        Assuming all verified courses have a minimum price of >0, this value should always
        be >0.

        If no verified mode is found, 0 is returned.
        """
        modes = cls.modes_for_course(course_id)
        for mode in modes:
            if (mode.currency.lower() == currency.lower()) and (mode.slug == 'verified'):
                return mode.min_price
        return 0

    @classmethod
    def has_verified_mode(cls, course_mode_dict):
        """Check whether the modes for a course allow a student to pursue a verified certificate.

        Args:
            course_mode_dict (dictionary mapping course mode slugs to Modes)

        Returns:
            bool: True iff the course modes contain a verified track.

        """
        for mode in cls.VERIFIED_MODES:
            if mode in course_mode_dict:
                return True
        return False

    @classmethod
    def has_professional_mode(cls, modes_dict):
        """
        check the course mode is profession or no-id-professional

        Args:
            modes_dict (dict): course modes.

        Returns:
            bool
        """
        return cls.PROFESSIONAL in modes_dict or cls.NO_ID_PROFESSIONAL_MODE in modes_dict

    @classmethod
    def contains_audit_mode(cls, modes_dict):
        """
        Check whether the modes_dict contains an audit mode.

        Args:
            modes_dict (dict): a dict of course modes

        Returns:
            bool: whether modes_dict contains an audit mode
        """
        return cls.AUDIT in modes_dict

    @classmethod
    def is_professional_mode(cls, course_mode_tuple):
        """
        checking that tuple is professional mode.
        Args:
            course_mode_tuple (tuple) : course mode tuple

        Returns:
            bool
        """
        return course_mode_tuple.slug in [cls.PROFESSIONAL, cls.NO_ID_PROFESSIONAL_MODE] if course_mode_tuple else False

    @classmethod
    def is_professional_slug(cls, slug):
        """checking slug is professional
        Args:
            slug (str) : course mode string
        Return:
            bool
        """
        return slug in [cls.PROFESSIONAL, cls.NO_ID_PROFESSIONAL_MODE]

    @classmethod
    def contains_masters_mode(cls, modes_dict):
        """
        Check whether the modes_dict contains a Master's mode.

        Args:
            modes_dict (dict): a dict of course modes

        Returns:
            bool: whether modes_dict contains a Master's mode
        """
        return cls.MASTERS in modes_dict

    @classmethod
    def is_masters_only(cls, course_id):
        """
        Check whether the course contains only a Master's mode.

        Args:
            course_id (CourseKey): course key of course to check

        Returns: bool: whether the course contains only a Master's mode
        """
        modes = cls.modes_for_course_dict(course_id)
        return cls.contains_masters_mode(modes) and len(modes) == 1

    @classmethod
    def is_mode_upgradeable(cls, mode_slug):
        """
        Returns True if the given mode can be upgraded to another.

        Note: Although, in practice, learners "upgrade" from verified to credit,
        that particular upgrade path is excluded by this method.
        """
        return mode_slug in cls.AUDIT_MODES

    @classmethod
    def is_verified_mode(cls, course_mode_tuple):
        """Check whether the given modes is_verified or not.

        Args:
            course_mode_tuple(Mode): Mode tuple

        Returns:
            bool: True iff the course modes is verified else False.

        """
        return course_mode_tuple.slug in cls.VERIFIED_MODES

    @classmethod
    def is_verified_slug(cls, mode_slug):
        """Check whether the given mode_slug is_verified or not.

        Args:
            mode_slug(str): Mode Slug

        Returns:
            bool: True iff the course mode slug is verified else False.

        """
        return mode_slug in cls.VERIFIED_MODES

    @classmethod
    def is_credit_eligible_slug(cls, mode_slug):
        """Check whether the given mode_slug is credit eligible or not.

        Args:
            mode_slug(str): Mode Slug

        Returns:
            bool: True iff the course mode slug is credit eligible else False.
        """
        return mode_slug in cls.CREDIT_ELIGIBLE_MODES

    @classmethod
    def is_credit_mode(cls, course_mode_tuple):
        """Check whether this is a credit mode.

        Students enrolled in a credit mode are eligible to
        receive university credit upon completion of a course.
        """
        return course_mode_tuple.slug in cls.CREDIT_MODES

    @classmethod
    def has_payment_options(cls, course_id):
        """Determines if there is any mode that has payment options

        Check the dict of course modes and see if any of them have a minimum price or
        suggested prices. Returns True if any course mode has a payment option.

        Args:
            course_mode_dict (dict): Dictionary mapping course mode slugs to Modes

        Returns:
            True if any course mode has a payment option.

        """
        for mode in cls.modes_for_course(course_id):
            if mode.min_price > 0 or mode.suggested_prices != '':
                return True
        return False

    @classmethod
    def can_auto_enroll(cls, course_id, modes_dict=None):
        """Check whether students should be auto-enrolled in the course.

        If a course is behind a paywall (e.g. professional ed or white-label),
        then users should NOT be auto-enrolled.  Instead, the user will
        be enrolled when he/she completes the payment flow.

        Otherwise, users can be enrolled in the default mode "honor"
        with the option to upgrade later.

        Args:
            course_id (CourseKey): The course to check.

        Keyword Args:
            modes_dict (dict): If provided, use these course modes.
                Useful for avoiding unnecessary database queries.

        Returns:
            bool

        """
        if modes_dict is None:
            modes_dict = cls.modes_for_course_dict(course_id)

        # Professional and no-id-professional mode courses are always behind a paywall
        if cls.has_professional_mode(modes_dict):
            return False

        # White-label uses course mode honor with a price
        # to indicate that the course is behind a paywall.
        if cls.is_white_label(course_id, modes_dict=modes_dict):
            return False

        # Check that a free mode is available.
        return cls.AUDIT in modes_dict or cls.HONOR in modes_dict

    @classmethod
    def auto_enroll_mode(cls, course_id, modes_dict=None):
        """
        return the auto-enrollable mode from given dict

        Args:
            modes_dict (dict): course modes.

        Returns:
            String: Mode name
        """
        if modes_dict is None:
            modes_dict = cls.modes_for_course_dict(course_id)

        if cls.HONOR in modes_dict:
            return cls.HONOR
        elif cls.AUDIT in modes_dict:
            return cls.AUDIT

    @classmethod
    def is_white_label(cls, course_id, modes_dict=None):
        """Check whether a course is a "white label" (paid) course.

        By convention, white label courses have a course mode slug "honor"
        and a price.

        Args:
            course_id (CourseKey): The course to check.

        Keyword Args:
            modes_dict (dict): If provided, use these course modes.
                Useful for avoiding unnecessary database queries.

        Returns:
            bool

        """
        if modes_dict is None:
            modes_dict = cls.modes_for_course_dict(course_id)

        # White-label uses course mode honor with a price
        # to indicate that the course is behind a paywall.
        if cls.HONOR in modes_dict and len(modes_dict) == 1:
            if modes_dict["honor"].min_price > 0 or modes_dict["honor"].suggested_prices != '':
                return True
        return False

    @classmethod
    def min_course_price_for_currency(cls, course_id, currency):
        """
        Returns the minimum price of the course in the appropriate currency over all the course's
        non-expired modes.
        If there is no mode found, will return the price of DEFAULT_MODE, which is 0
        """
        modes = cls.modes_for_course(course_id)
        return min(
            (mode.min_price for mode in modes if mode.currency.lower() == currency.lower()),
            default=0
        )

    @classmethod
    def is_eligible_for_certificate(cls, mode_slug, status=None):
        """
        Returns whether or not the given mode_slug is eligible for a
        certificate. Currently all modes other than 'audit' grant a
        certificate. Note that audit enrollments which existed prior
        to December 2015 *were* given certificates, so there will be
        GeneratedCertificate records with mode='audit' which are
        eligible.
        """
        ineligible_modes = [cls.AUDIT]

        if settings.FEATURES.get('DISABLE_HONOR_CERTIFICATES', False):
            # Adding check so that we can regenerate the certificate for learners who have
            # already earned the certificate using honor mode
            from lms.djangoapps.certificates.data import CertificateStatuses
            if mode_slug == cls.HONOR and status != CertificateStatuses.downloadable:
                ineligible_modes.append(cls.HONOR)

        return mode_slug not in ineligible_modes

    def to_tuple(self):
        """
        Takes a mode model and turns it into a model named tuple.

        Returns:
            A 'Mode' namedtuple with all the same attributes as the model.

        """
        return Mode(
            self.mode_slug,
            self.mode_display_name,
            self.min_price,
            self.suggested_prices,
            self.currency,
            self.expiration_datetime,
            self.description,
            self.sku,
            self.android_sku,
            self.ios_sku,
            self.bulk_sku
        )

    def __str__(self):
        return "{} : {}, min={}".format(
            self.course_id, self.mode_slug, self.min_price
        )


@receiver(models.signals.post_save, sender=CourseMode)
@receiver(models.signals.post_delete, sender=CourseMode)
def invalidate_course_mode_cache(sender, **kwargs):   # pylint: disable=unused-argument
    """Invalidate the cache of course modes. """
    RequestCache(namespace=CourseMode.CACHE_NAMESPACE).clear()


def get_cosmetic_verified_display_price(course):
    """
    Returns the minimum verified cert course price as a string preceded by correct currency, or 'Free'.
    """
    return get_course_prices(course, verified_only=True)[1]


def get_cosmetic_display_price(course):
    """
    Returns the course price as a string preceded by correct currency, or 'Free'.
    """
    return get_course_prices(course)[1]


def get_course_prices(course, verified_only=False):
    """
    Return registration_price and cosmetic_display_prices.
    registration_price is the minimum price for the course across all course modes.
    cosmetic_display_prices is the course price as a string preceded by correct currency, or 'Free'.
    """
    # Find the
    if verified_only:
        registration_price = CourseMode.min_course_price_for_verified_for_currency(
            course.id,
            settings.PAID_COURSE_REGISTRATION_CURRENCY[0]
        )
    else:
        registration_price = CourseMode.min_course_price_for_currency(
            course.id,
            settings.PAID_COURSE_REGISTRATION_CURRENCY[0]
        )

    if registration_price > 0:
        price = registration_price
    # Handle course overview objects which have no cosmetic_display_price
    elif hasattr(course, 'cosmetic_display_price'):
        price = course.cosmetic_display_price
    else:
        price = None

    return registration_price, format_course_price(price)


def format_course_price(price):
    """
    Return a formatted price for a course (a string preceded by correct currency, or 'Free').
    """
    currency_symbol = settings.PAID_COURSE_REGISTRATION_CURRENCY[1]

    if price:
        # Translators: This will look like '$50', where {currency_symbol} is a symbol such as '$' and {price} is a
        # numerical amount in that currency. Adjust this display as needed for your language.
        cosmetic_display_price = _("{currency_symbol}{price}").format(currency_symbol=currency_symbol, price=price)
    else:
        # Translators: This refers to the cost of the course. In this case, the course costs nothing so it is free.
        cosmetic_display_price = _('Free')

    return cosmetic_display_price


class CourseModesArchive(models.Model):
    """
    Store the past values of course_mode that a course had in the past. We decided on having
    separate model, because there is a uniqueness contraint on (course_mode, course_id)
    field pair in CourseModes. Having a separate table allows us to have an audit trail of any changes
    such as course price changes

    .. no_pii:
    """
    class Meta:
        app_label = "course_modes"

    # the course that this mode is attached to
    course_id = CourseKeyField(db_index=True)

    # the reference to this mode that can be used by Enrollments to generate
    # similar behavior for the same slug across courses
    mode_slug = models.CharField(max_length=100)

    # The 'pretty' name that can be translated and displayed
    mode_display_name = models.CharField(max_length=255)

    # minimum price in USD that we would like to charge for this mode of the course
    min_price = models.IntegerField(default=0)

    # the suggested prices for this mode
    suggested_prices = models.CharField(max_length=255, blank=True, default='',
                                        validators=[validate_comma_separated_integer_list])

    # the currency these prices are in, using lower case ISO currency codes
    currency = models.CharField(default="usd", max_length=8)

    # turn this mode off after the given expiration date
    expiration_date = models.DateField(default=None, null=True, blank=True)

    expiration_datetime = models.DateTimeField(default=None, null=True, blank=True)


class CourseModeExpirationConfig(ConfigurationModel):
    """
    Configuration for time period from end of course to auto-expire a course mode.

    .. no_pii:
    """
    class Meta:
        app_label = "course_modes"

    verification_window = models.DurationField(
        default=timedelta(days=10),
        help_text=_(
            "The time period before a course ends in which a course mode will expire"
        )
    )

    def __str__(self):
        """ Returns the unicode date of the verification window. """
        return str(self.verification_window)
