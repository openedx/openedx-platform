"""
Declaration of CourseOverview model
"""


import json
import logging
from datetime import datetime
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

from ccx_keys.locator import CCXLocator
from config_models.models import ConfigurationModel
from django.conf import settings
from django.db import models, transaction
from django.db.models import Q
from django.db.models.signals import post_save, post_delete
from django.db.utils import IntegrityError
from django.template import defaultfilters

from django.utils.functional import cached_property
from model_utils.models import TimeStampedModel
from opaque_keys.edx.django.models import CourseKeyField, UsageKeyField
from simple_history.models import HistoricalRecords

from lms.djangoapps.courseware.model_data import FieldDataCache
from openedx.core.djangoapps.catalog.models import CatalogIntegration
from openedx.core.djangoapps.lang_pref.api import get_closest_released_language
from openedx.core.djangoapps.models.course_details import CourseDetails
from openedx.core.lib.cache_utils import request_cached, RequestCache
from common.djangoapps.static_replace.models import AssetBaseUrlConfig
from xmodule import block_metadata_utils, course_metadata_utils  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.course_block import DEFAULT_START_DATE, CourseBlock  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.error_block import ErrorBlock  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.tabs import CourseTab  # lint-amnesty, pylint: disable=wrong-import-order

log = logging.getLogger(__name__)


class CourseOverviewCaseMismatchException(Exception):
    pass


class CourseOverview(TimeStampedModel):
    """
    Model for storing and caching basic information about a course.

    This model contains basic course metadata such as an ID, display name,
    image URL, and any other information that would be necessary to display
    a course as part of:
        user dashboard (enrolled courses)
        course catalog (courses to enroll in)
        course about (meta data about the course)

    When you bump the VERSION you will invalidate all existing course overviews. This
    will cause a slew of modulestore reads as each course needs to be re-cached into
    the course overview.

    .. no_pii:
    """

    class Meta:
        app_label = 'course_overviews'

    # IMPORTANT: Bump this whenever you modify this model and/or add a migration.
    VERSION = 19

    # Cache entry versioning.
    version = models.IntegerField()

    # Course identification
    id = CourseKeyField(db_index=True, primary_key=True, max_length=255)
    _location = UsageKeyField(max_length=255)
    org = models.TextField(max_length=255, default='outdated_entry')
    display_name = models.TextField(null=True)
    display_number_with_default = models.TextField()
    display_org_with_default = models.TextField()

    start = models.DateTimeField(null=True)
    end = models.DateTimeField(null=True)

    # These are deprecated and unused, but cannot be dropped via simple migration due to the size of the downstream
    # history table. See DENG-19 for details.
    # Please use start and end above for these values.
    start_date = models.DateTimeField(null=True)
    end_date = models.DateTimeField(null=True)

    advertised_start = models.TextField(null=True)
    announcement = models.DateTimeField(null=True)

    # URLs
    # Not allowing null per django convention; not sure why many TextFields in this model do allow null
    banner_image_url = models.TextField()
    course_image_url = models.TextField()
    social_sharing_url = models.TextField(null=True)
    end_of_course_survey_url = models.TextField(null=True)

    # Certification data
    certificates_display_behavior = models.TextField(null=True)
    certificates_show_before_end = models.BooleanField(default=False)
    cert_html_view_enabled = models.BooleanField(default=False)
    has_any_active_web_certificate = models.BooleanField(default=False)
    cert_name_short = models.TextField()
    cert_name_long = models.TextField()
    certificate_available_date = models.DateTimeField(default=None, null=True)

    # Grading
    lowest_passing_grade = models.DecimalField(max_digits=5, decimal_places=2, null=True)

    # Access parameters
    days_early_for_beta = models.FloatField(null=True)
    mobile_available = models.BooleanField(default=False)
    visible_to_staff_only = models.BooleanField(default=False)
    _pre_requisite_courses_json = models.TextField()  # JSON representation of list of CourseKey strings

    # Enrollment details
    enrollment_start = models.DateTimeField(null=True)
    enrollment_end = models.DateTimeField(null=True)
    enrollment_domain = models.TextField(null=True)
    invitation_only = models.BooleanField(default=False)
    max_student_enrollments_allowed = models.IntegerField(null=True)

    # Catalog information
    catalog_visibility = models.TextField(null=True)
    short_description = models.TextField(null=True)
    course_video_url = models.TextField(null=True)
    effort = models.TextField(null=True)
    self_paced = models.BooleanField(default=False)
    marketing_url = models.TextField(null=True)
    eligible_for_financial_aid = models.BooleanField(default=True)

    # Course highlight info, used to guide course update emails
    has_highlights = models.BooleanField(null=True, default=None)  # if None, you have to look up the answer yourself

    # Proctoring
    enable_proctored_exams = models.BooleanField(default=False)
    proctoring_provider = models.TextField(null=True)
    proctoring_escalation_email = models.TextField(null=True)
    allow_proctoring_opt_out = models.BooleanField(default=False)

    # Entrance Exam information
    entrance_exam_enabled = models.BooleanField(default=False)
    entrance_exam_id = models.CharField(max_length=255, blank=True)
    entrance_exam_minimum_score_pct = models.FloatField(default=0.65)

    # Open Response Assessment configuration
    force_on_flexible_peer_openassessments = models.BooleanField(default=False)

    external_id = models.CharField(max_length=128, null=True, blank=True)

    language = models.TextField(null=True)

    history = HistoricalRecords()

    @classmethod
    def _create_or_update(cls, course):  # lint-amnesty, pylint: disable=too-many-statements
        """
        Creates or updates a CourseOverview object from a CourseBlock.

        Does not touch the database, simply constructs and returns an overview
        from the given course.

        Arguments:
            course (CourseBlock): any course block object

        Returns:
            CourseOverview: created or updated overview extracted from the given course
        """
        from lms.djangoapps.certificates.api import get_active_web_certificate
        from openedx.core.lib.courses import course_image_url

        # Workaround for a problem discovered in https://openedx.atlassian.net/browse/TNL-2806.
        # If the course has a malformed grading policy such that
        # course._grading_policy['GRADE_CUTOFFS'] = {}, then
        # course.lowest_passing_grade will raise a ValueError.
        # Work around this for now by defaulting to None.
        try:
            lowest_passing_grade = course.lowest_passing_grade
        except ValueError:
            lowest_passing_grade = None

        display_name = course.display_name
        start = course.start
        end = course.end
        max_student_enrollments_allowed = course.max_student_enrollments_allowed
        if isinstance(course.id, CCXLocator):
            from lms.djangoapps.ccx.utils import get_ccx_from_ccx_locator
            ccx = get_ccx_from_ccx_locator(course.id)
            display_name = ccx.display_name
            start = ccx.start
            end = ccx.due
            max_student_enrollments_allowed = ccx.max_student_enrollments_allowed

        course_overview = cls.objects.filter(id=course.id)
        if course_overview.exists():
            log.info('Updating course overview for %s.', str(course.id))
            course_overview = course_overview.first()
            # MySQL ignores casing, but CourseKey doesn't. To prevent multiple
            # courses with different cased keys from overriding each other, we'll
            # check for equality here in python.
            if course_overview.id != course.id:
                raise CourseOverviewCaseMismatchException(course_overview.id, course.id)
        else:
            log.info('Creating course overview for %s.', str(course.id))
            course_overview = cls()

        course_overview.version = cls.VERSION
        course_overview.id = course.id
        course_overview._location = course.location  # lint-amnesty, pylint: disable=protected-access
        course_overview.org = course.location.org
        course_overview.display_name = display_name
        course_overview.display_number_with_default = course.display_number_with_default
        course_overview.display_org_with_default = course.display_org_with_default

        course_overview.start = start
        course_overview.end = end
        course_overview.advertised_start = course.advertised_start
        course_overview.announcement = course.announcement

        course_overview.banner_image_url = course_image_url(course, 'banner_image')
        course_overview.course_image_url = course_image_url(course)
        course_overview.social_sharing_url = course.social_sharing_url

        updated_available_date, updated_display_behavior = CourseDetails.validate_certificate_settings(
            course.certificate_available_date,
            course.certificates_display_behavior
        )

        course_overview.certificates_display_behavior = updated_display_behavior
        course_overview.certificate_available_date = updated_available_date
        course_overview.certificates_show_before_end = course.certificates_show_before_end
        course_overview.cert_html_view_enabled = course.cert_html_view_enabled
        course_overview.has_any_active_web_certificate = (get_active_web_certificate(course) is not None)
        course_overview.cert_name_short = course.cert_name_short
        course_overview.cert_name_long = course.cert_name_long
        course_overview.lowest_passing_grade = lowest_passing_grade
        course_overview.end_of_course_survey_url = course.end_of_course_survey_url

        course_overview.days_early_for_beta = course.days_early_for_beta
        course_overview.mobile_available = course.mobile_available
        course_overview.visible_to_staff_only = course.visible_to_staff_only
        course_overview._pre_requisite_courses_json = json.dumps(course.pre_requisite_courses)  # lint-amnesty, pylint: disable=protected-access

        course_overview.enrollment_start = course.enrollment_start
        course_overview.enrollment_end = course.enrollment_end
        course_overview.enrollment_domain = course.enrollment_domain
        course_overview.invitation_only = course.invitation_only
        course_overview.max_student_enrollments_allowed = max_student_enrollments_allowed

        course_overview.catalog_visibility = course.catalog_visibility
        course_overview.short_description = CourseDetails.fetch_about_attribute(course.id, 'short_description')
        course_overview.effort = CourseDetails.fetch_about_attribute(course.id, 'effort')
        course_overview.course_video_url = CourseDetails.fetch_video_url(course.id)
        course_overview.self_paced = course.self_paced

        course_overview.has_highlights = cls._get_course_has_highlights(course)

        course_overview.enable_proctored_exams = course.enable_proctored_exams
        course_overview.proctoring_provider = course.proctoring_provider
        course_overview.proctoring_escalation_email = course.proctoring_escalation_email
        course_overview.allow_proctoring_opt_out = course.allow_proctoring_opt_out

        course_overview.entrance_exam_enabled = course.entrance_exam_enabled
        # entrance_exam_id defaults to None in the course object, but '' is more reasonable for a string field
        course_overview.entrance_exam_id = course.entrance_exam_id or ''
        # Despite it being a float, the course object defaults to an int. So we will detect that case and update
        # it to be a float like everything else.
        # Extra handling: entrance_exam_minimum_score_pct can be None (e.g. when exams are disabled in Studio),
        # so we fall back to settings.ENTRANCE_EXAM_MIN_SCORE_PCT to prevent CourseOverview save failures.
        if course.entrance_exam_minimum_score_pct is None:
            entrance_exam_minimum_score_pct = float(settings.ENTRANCE_EXAM_MIN_SCORE_PCT)
        else:
            entrance_exam_minimum_score_pct = course.entrance_exam_minimum_score_pct

        if (
            isinstance(entrance_exam_minimum_score_pct, int)
            or (isinstance(entrance_exam_minimum_score_pct, float) and entrance_exam_minimum_score_pct.is_integer())
        ):
            entrance_exam_minimum_score_pct = entrance_exam_minimum_score_pct / 100

        course_overview.entrance_exam_minimum_score_pct = entrance_exam_minimum_score_pct

        course_overview.force_on_flexible_peer_openassessments = course.force_on_flexible_peer_openassessments

        if not CatalogIntegration.is_enabled():
            course_overview.language = course.language

        return course_overview

    @classmethod
    def load_from_module_store(cls, course_id):
        """
        Load a CourseBlock, create or update a CourseOverview from it, cache the
        overview, and return it.

        Arguments:
            course_id (CourseKey): the ID of the course overview to be loaded.

        Returns:
            CourseOverview: overview of the requested course.

        Raises:
            - CourseOverview.DoesNotExist if the course specified by course_id
                was not found.
            - IOError if some other error occurs while trying to load the
                course from the module store.
        """
        log.info(
            "Attempting to load CourseOverview for course %s from modulestore.",
            course_id,
        )
        store = modulestore()
        with store.bulk_operations(course_id):
            course = store.get_course(course_id)
            if isinstance(course, CourseBlock):
                try:
                    course_overview = cls._create_or_update(course)
                    with transaction.atomic():
                        course_overview.save()
                        # Remove and recreate all the course tabs
                        CourseOverviewTab.objects.filter(course_overview=course_overview).delete()
                        CourseOverviewTab.objects.bulk_create([
                            CourseOverviewTab(
                                tab_id=tab.tab_id,
                                type=tab.type,
                                name=tab.name,
                                course_staff_only=tab.course_staff_only,
                                url_slug=tab.get('url_slug'),
                                link=tab.get('link'),
                                is_hidden=tab.get('is_hidden', False),
                                course_overview=course_overview)
                            for tab in course.tabs
                        ])
                        # Remove and recreate course images
                        CourseOverviewImageSet.objects.filter(course_overview=course_overview).delete()
                        CourseOverviewImageSet.create(course_overview, course)

                except IntegrityError:
                    # There is a rare race condition that will occur if
                    # CourseOverview.get_from_id is called while a
                    # another identical overview is already in the process
                    # of being created.
                    # One of the overviews will be saved normally, while the
                    # other one will cause an IntegrityError because it tries
                    # to save a duplicate.
                    # (see: https://openedx.atlassian.net/browse/TNL-2854).
                    log.info(
                        "Multiple CourseOverviews for course %s requested "
                        "simultaneously; will only save one.",
                        course_id,
                    )
                except Exception:
                    log.exception(
                        "Saving CourseOverview for course %s failed with "
                        "unexpected exception!",
                        course_id,
                    )
                    raise

                return course_overview
            elif course is not None:
                raise OSError(  # lint-amnesty, pylint: disable=raising-format-tuple
                    "Error while loading CourseOverview for course {} "
                    "from the module store: {}",
                    str(course_id),
                    course.error_msg if isinstance(course, ErrorBlock) else str(course)
                )
            else:
                log.info(
                    "Could not create CourseOverview for non-existent course: %s",
                    course_id,
                )
                raise cls.DoesNotExist()

    @classmethod
    def course_exists(cls, course_id):
        """
        Check whether a course run exists (in CourseOverviews _or_ modulestore).

        Checks the CourseOverview table first.
        If it is not there, check the modulestore.
        Equivalent to, but more efficient than:
            bool(CourseOverview.get_from_id(course_id))

        Arguments:
            course_id (CourseKey)

        Returns: bool
        """
        if cls.objects.filter(id=course_id).exists():
            return True
        return modulestore().has_course(course_id)

    @classmethod
    @request_cached('course_overview')
    def get_from_id(cls, course_id):
        """
        Load a CourseOverview object for a given course ID.

        First, we try to load the CourseOverview from the database. If it
        doesn't exist, we load the entire course from the modulestore, create a
        CourseOverview object from it, and then cache it in the database for
        future use.

        Arguments:
            course_id (CourseKey): the ID of the course overview to be loaded.

        Returns:
            CourseOverview: overview of the requested course.

        Raises:
            - CourseOverview.DoesNotExist if the course specified by course_id
                was not found.
            - IOError if some other error occurs while trying to load the
                course from the module store.
        """
        try:
            course_overview = cls.objects.select_related('image_set').get(id=course_id)
            if course_overview.version < cls.VERSION:
                # Reload the overview from the modulestore to update the version
                course_overview = cls.load_from_module_store(course_id)
        except cls.DoesNotExist:
            course_overview = None

        # Regenerate the thumbnail images if they're missing (either because
        # they were never generated, or because they were flushed out after
        # a change to CourseOverviewImageConfig.
        if course_overview and not hasattr(course_overview, 'image_set'):
            CourseOverviewImageSet.create(course_overview)

        return course_overview or cls.load_from_module_store(course_id)

    @classmethod
    def get_from_ids(cls, course_ids):
        """
        Return a dict mapping course_ids to CourseOverviews.

        Tries to select all CourseOverviews in one query,
        then fetches remaining (uncached) overviews from the modulestore.

        Course IDs for non-existant courses will map to None.

        Arguments:
            course_ids (iterable[CourseKey])

        Returns: dict[CourseKey, CourseOverview|None]
        """
        overviews = {
            overview.id: overview
            for overview in cls.objects.select_related('image_set').filter(
                id__in=course_ids,
                version__gte=cls.VERSION
            )
        }
        for course_id in course_ids:
            if course_id not in overviews:
                try:
                    overviews[course_id] = cls.load_from_module_store(course_id)
                except CourseOverview.DoesNotExist:
                    overviews[course_id] = None
        return overviews

    @classmethod
    def _get_course_has_highlights(cls, course):
        # Avoid circular import here
        from openedx.core.djangoapps.schedules.content_highlights import course_has_highlights
        return course_has_highlights(course)

    def clean_id(self, padding_char='='):
        """
        Returns a unique deterministic base32-encoded ID for the course.

        Arguments:
            padding_char (str): Character used for padding at end of base-32
                                -encoded string, defaulting to '='
        """
        return course_metadata_utils.clean_course_key(self.location.course_key, padding_char)

    @property
    def usage_key(self):
        """
        Returns the UsageKey of the root block of this course.

        UsageKeyField has a strange behavior where it fails to parse the "run"
        of a course out of the serialized form of a Mongo Draft UsageKey. This
        method is a wrapper around _location attribute that fixes the problem
        by calling map_into_course, which restores the run attribute.
        """
        if self._location.run is None:
            self._location = self._location.map_into_course(self.id)
        return self._location

    @property
    def location(self):
        """
        The old name for `usage_key`. This method is analogous to `XModuleMixin.location`.
        """
        return self.usage_key

    @property
    def number(self):
        """
        Returns this course's number.

        This is a "number" in the sense of the "course numbers" that you see at
        lots of universities. For example, given a course
        "Intro to Computer Science" with the course key "edX/CS-101/2014", the
        course number would be "CS-101"
        """
        return course_metadata_utils.number_for_course_location(self.location)

    @property
    def url_name(self):
        """
        The old name for `block_id`. This method is analogous to `XModuleMixin.url_name`.
        """
        return self.usage_key.block_id

    @property
    def display_name_with_default(self):
        """
        Return reasonable display name for the course.
        """
        return block_metadata_utils.display_name_with_default(self)

    @property
    def display_name_with_default_escaped(self):
        """
        DEPRECATED: use display_name_with_default

        Return html escaped reasonable display name for the course.

        Note: This newly introduced method should not be used.  It was only
        introduced to enable a quick search/replace and the ability to slowly
        migrate and test switching to display_name_with_default, which is no
        longer escaped.
        """
        # pylint: disable=line-too-long
        return block_metadata_utils.display_name_with_default_escaped(self)  # xss-lint: disable=python-deprecated-display-name

    @property
    def dashboard_start_display(self):
        """
         Return start date to diplay on learner's dashboard, preferably `Course Advertised Start`
        """
        return self.advertised_start or self.start

    def has_started(self):
        """
        Returns whether the the course has started.
        """
        return course_metadata_utils.has_course_started(self.start)

    def has_ended(self):
        """
        Returns whether the course has ended.
        """
        return course_metadata_utils.has_course_ended(self.end)

    def is_enrollment_open(self):
        """
        Returns True if course enrollment is open
        """
        return course_metadata_utils.is_enrollment_open(self.enrollment_start, self.enrollment_end)

    def has_marketing_url(self):
        """
        Returns whether the course has marketing url.
        """
        return settings.FEATURES.get('ENABLE_MKTG_SITE') and bool(self.marketing_url)

    def has_social_sharing_url(self):
        """
        Returns whether the course has social sharing url.
        """
        is_social_sharing_enabled = getattr(settings, 'SOCIAL_SHARING_SETTINGS', {}).get('CUSTOM_COURSE_URLS')
        return is_social_sharing_enabled and bool(self.social_sharing_url)

    def starts_within(self, days):
        """
        Returns True if the course starts with-in given number of days otherwise returns False.
        """
        return course_metadata_utils.course_starts_within(self.start, days)

    @property
    def start_date_is_still_default(self):
        """
        Checks if the start date set for the course is still default, i.e.
        .start has not been modified, and .advertised_start has not been set.
        """
        return course_metadata_utils.course_start_date_is_default(
            self.start,
            self.advertised_start,
        )

    @property
    def sorting_score(self):
        """
        Returns a tuple that can be used to sort the courses according
        the how "new" they are. The "newness" score is computed using a
        heuristic that takes into account the announcement and
        (advertised) start dates of the course if available.

        The lower the number the "newer" the course.
        """
        return course_metadata_utils.sorting_score(self.start, self.advertised_start, self.announcement)

    @property
    def start_type(self):
        """
        Returns the type of the course's 'start' field.
        """
        if self.advertised_start:
            return 'string'
        elif self.start != DEFAULT_START_DATE:
            return 'timestamp'
        else:
            return 'empty'

    @property
    def start_display(self):
        """
        Returns the display value for the course's start date.
        """
        if self.advertised_start:
            return self.advertised_start
        elif self.start != DEFAULT_START_DATE:
            return defaultfilters.date(self.start, "DATE_FORMAT")
        else:
            return None

    @property
    def pre_requisite_courses(self):
        """
        Returns a list of ID strings for this course's prerequisite courses.
        """
        return json.loads(self._pre_requisite_courses_json)

    @pre_requisite_courses.setter
    def pre_requisite_courses(self, value):
        """
        Django requires there be a setter for this, but it is not
        necessary for the way we currently use it. Due to the way
        CourseOverviews are constructed raising errors here will
        cause a lot of issues. These should not be mutable after
        construction, so for now we just eat this.
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @classmethod
    def update_select_courses(cls, course_keys, force_update=False):
        """
        A side-effecting method that updates CourseOverview objects for
        the given course_keys.

        Arguments:
            course_keys (list[CourseKey]): Identifies for which courses to
                return CourseOverview objects.
            force_update (boolean): Optional parameter that indicates
                whether the requested CourseOverview objects should be
                forcefully updated (i.e., re-synched with the modulestore).
        """
        log.info('Generating course overview for %d courses.', len(course_keys))
        log.debug('Generating course overview(s) for the following courses: %s', course_keys)

        action = CourseOverview.load_from_module_store if force_update else CourseOverview.get_from_id

        for course_key in course_keys:
            try:
                action(course_key)
            except Exception as ex:  # pylint: disable=broad-except
                log.exception(
                    'An error occurred while generating course overview for %s: %s',
                    str(course_key),
                    str(ex),
                )

        log.info('Finished generating course overviews.')

    @classmethod
    def get_all_courses(cls, orgs=None, filter_=None, active_only=False, course_keys=None):
        """
        Return a queryset containing all CourseOverview objects in the database.

        Arguments:
            orgs (list[string]): Optional parameter that allows case-insensitive
                filtering by organization.
            filter_ (dict): Optional parameter that allows custom filtering.
            active_only (bool): If provided, only the courses that have not ended will be returned.
            course_keys (list[string]): Optional parameter that allows case-insensitive
                filter by course ids
        """
        # Note: If a newly created course is not returned in this QueryList,
        # make sure the "publish" signal was emitted when the course was
        # created. For tests using CourseFactory, use emit_signals=True.
        course_overviews = CourseOverview.objects.all()

        if course_keys:
            course_overviews = course_overviews.filter(id__in=course_keys)

        if orgs:
            # In rare cases, courses belonging to the same org may be accidentally assigned
            # an org code with a different casing (e.g., Harvardx as opposed to HarvardX).
            # Case-insensitive matching allows us to deal with this kind of dirty data.
            org_filter = Q()  # Avoiding the `reduce()` for more readability, so a no-op filter starter is needed.
            for org in orgs:
                org_filter |= Q(org__iexact=org)
            course_overviews = course_overviews.filter(org_filter)

        if filter_:
            course_overviews = course_overviews.filter(**filter_)
        if active_only:
            course_overviews = course_overviews.filter(
                Q(end__isnull=True) | Q(end__gte=datetime.now().replace(tzinfo=ZoneInfo("UTC")))
            )

        return course_overviews

    @classmethod
    def get_courses_matching_query(cls, query, course_overviews):
        """
        Return a queryset of CourseOverview objects filtered bythe given query.

        Args:
            query: required parameter that allows filtering based on the CourseOverview.
            course_overviews: queryset of CourseOverview objects to filter on.
        """
        return course_overviews.filter(
            Q(display_name__icontains=query) |
            Q(org__icontains=query) |
            Q(id__icontains=query)
        )

    @classmethod
    def get_courses_by_status(cls, active_only, archived_only, course_overviews):
        """
        Return a queryset of CourseOverview objects based on the given status.

        Args:
            active_only: when True, only active courses will be returned.
            archived_only: when True, only archived courses will be returned.
            course_overviews: queryset of CourseOverview objects to filter on.
        """
        if active_only:
            return course_overviews.filter(
                Q(end__isnull=True) | Q(end__gte=datetime.now().replace(tzinfo=ZoneInfo("UTC")))
            )
        if archived_only:
            return course_overviews.filter(
                end__lt=datetime.now().replace(tzinfo=ZoneInfo("UTC"))
            )
        return course_overviews

    @classmethod
    def get_all_course_keys(cls):
        """
        Returns all course keys from course overviews.
        """
        return CourseOverview.objects.values_list('id', flat=True)

    def is_discussion_tab_enabled(self, user=None):
        """
        Returns True if course has discussion tab and is enabled
        """
        # Importing here to avoid circular import
        from lms.djangoapps.discussion.plugins import DiscussionTab
        tabs = self.tab_set.all()
        # creates circular import; hence explicitly referenced is_discussion_enabled
        for tab in tabs:
            if tab.tab_id == "discussion":
                return DiscussionTab.is_enabled(self, user)
        return False

    @property
    def tabs(self):
        """
        Returns an iterator of CourseTabs.
        """
        for tab_dict in self.tab_set.all().values():
            tab = CourseTab.from_json(tab_dict)
            if tab is None:
                log.warning("Can't instantiate CourseTab from %r", tab_dict)
            else:
                yield tab

    @property
    def image_urls(self):
        """
        Return a dict with all known URLs for this course image.

        Current resolutions are:
          raw = original upload from the user
          small = thumbnail with dimensions CourseOverviewImageConfig.current().small
          large = thumbnail with dimensions CourseOverviewImageConfig.current().large

        If no thumbnails exist, the raw (originally uploaded) image will be
        returned for all resolutions.
        """
        # This is either the raw image that the course team uploaded, or the
        # settings.DEFAULT_COURSE_ABOUT_IMAGE_URL if they didn't specify one.
        raw_image_url = self.course_image_url

        # Default all sizes to return the raw image if there is no
        # CourseOverviewImageSet associated with this CourseOverview. This can
        # happen because we're disabled via CourseOverviewImageConfig.
        urls = {
            'raw': raw_image_url,
            'small': raw_image_url,
            'large': raw_image_url,
        }

        # If we do have a CourseOverviewImageSet, we still default to the raw
        # images if our thumbnails are blank (might indicate that there was a
        # processing error of some sort while trying to generate thumbnails).
        if hasattr(self, 'image_set') and CourseOverviewImageConfig.current().enabled:
            urls['small'] = self.image_set.small_url or raw_image_url
            urls['large'] = self.image_set.large_url or raw_image_url

        return self.apply_cdn_to_urls(urls)

    @property
    def pacing(self):
        """ Returns the pacing for the course.

        Potential values:
            self: Self-paced courses
            instructor: Instructor-led courses
        """
        return 'self' if self.self_paced else 'instructor'

    @property
    def closest_released_language(self):
        """
        Returns the language code that most closely matches this course' language and is fully
        supported by the LMS, or None if there are no fully supported languages that
        match the target.
        """
        return get_closest_released_language(self.language) if self.language else None

    def apply_cdn_to_url(self, image_url):
        """
        Applies a new CDN/base URL to the given URLs if CDN configuration is
        enabled.

        If CDN does not exist or is disabled, just returns the original. The
        URL that we store in CourseOverviewImageSet is already top level path,
        so we don't need to go through the /static remapping magic that happens
        with other course assets. We just need to add the CDN server if appropriate.
        """
        cdn_config = AssetBaseUrlConfig.current()
        if not cdn_config.enabled:
            return image_url

        return self._apply_cdn_to_url(image_url, cdn_config.base_url)

    def apply_cdn_to_urls(self, image_urls):
        """
        Given a dict of resolutions -> urls, return a copy with CDN applied.

        If CDN does not exist or is disabled, just returns the original. The
        URLs that we store in CourseOverviewImageSet are all already top level
        paths, so we don't need to go through the /static remapping magic that
        happens with other course assets. We just need to add the CDN server if
        appropriate.
        """
        return {
            resolution: self.apply_cdn_to_url(url)
            for resolution, url in image_urls.items()
        }

    def _apply_cdn_to_url(self, url, base_url):
        """
        Applies a new CDN/base URL to the given URL.

        If a URL is absolute, we skip switching the host since it could
        be a hostname that isn't behind our CDN, and we could unintentionally
        break the URL overall.
        """

        # The URL can't be empty.
        if not url:
            return url

        _, netloc, path, params, query, fragment = urlparse(url)

        # If this is an absolute URL, just return it as is.  It could be a domain
        # that isn't ours, and thus CDNing it would actually break it.
        if netloc:
            return url

        return urlunparse(('', base_url, path, params, query, fragment))

    @cached_property
    def _original_course(self):
        """
        Returns the course from the modulestore.
        """
        return modulestore().get_course(self.id)

    def bind_course_for_student(self, request):
        """
        Bind user-specific field data to the Course XBlock.

        By default, the retrieved course XBlock is "unbound" - it means that any field from the `user_info` scope
        (like `edxnotes_visibility`) returns its default value.
        """
        # Delay import until here to avoid circular dependency.
        from lms.djangoapps.courseware.block_render import get_block_for_descriptor
        get_block_for_descriptor(
            request.user,
            request,
            self._original_course,
            FieldDataCache([self._original_course], self._original_course.id, request.user),
            self._original_course.id,
        )

    @property
    def allow_public_wiki_access(self):
        """
        TODO: move this to the model.
        """
        return self._original_course.allow_public_wiki_access

    @property
    def textbooks(self):
        """
        TODO: move this to the model.
        """
        return self._original_course.textbooks

    @property
    def pdf_textbooks(self):
        """
        TODO: move this to the model.
        """
        return self._original_course.pdf_textbooks

    @property
    def html_textbooks(self):
        """
        TODO: move this to the model.
        """
        return self._original_course.html_textbooks

    @property
    def hide_progress_tab(self):
        """
        TODO: move this to the model.
        """
        return self._original_course.hide_progress_tab

    @property
    def edxnotes(self):
        """
        TODO: move this to the model.
        """
        return self._original_course.edxnotes

    @property
    def enable_ccx(self):
        """
        TODO: move this to the model.
        """
        return self._original_course.enable_ccx

    @property
    def course_visibility(self):
        """
        TODO: move this to the model.
        """
        return self._original_course.course_visibility

    @property
    def teams_enabled(self):
        """
        TODO: move this to the model.
        """
        return self._original_course.teams_enabled

    @property
    def show_calculator(self):
        """
        TODO: move this to the model.
        """
        return self._original_course.show_calculator

    @property
    def edxnotes_visibility(self):
        """
        TODO: move this to the model.
        """
        return self._original_course.edxnotes_visibility

    def __str__(self):
        """Represent ourselves with the course key."""
        return str(self.id)


class CourseOverviewTab(models.Model):
    """
    Model for storing and caching tabs information of a course.

    .. no_pii:
    """
    tab_id = models.CharField(max_length=50)
    course_overview = models.ForeignKey(CourseOverview, db_index=True, related_name="tab_set", on_delete=models.CASCADE)
    type = models.CharField(max_length=50, null=True)
    name = models.TextField(null=True)
    course_staff_only = models.BooleanField(default=False)
    url_slug = models.TextField(null=True)
    link = models.TextField(null=True)
    is_hidden = models.BooleanField(default=False)

    def __str__(self):
        return self.tab_id


class CourseOverviewImageSet(TimeStampedModel):
    """
    Model for Course overview images. Each column is an image type/size.

    You should basically never use this class directly. Read from
    CourseOverview.image_urls instead.

    Special Notes on Deployment/Rollback/Changes:

    1. By default, this functionality is disabled. To turn it on, you have to
       create a CourseOverviewImageConfig entry via Django Admin and select
       enabled=True.

    2. If it is enabled in configuration, it will lazily create thumbnails as
       individual CourseOverviews are requested. This is independent of the
       CourseOverview's cls.VERSION scheme. This is to better support the use
       case where someone might want to change the thumbnail resolutions for
       their theme -- we didn't want to tie the code-based data schema of
       CourseOverview to configuration changes.

    3. A CourseOverviewImageSet is automatically deleted when the CourseOverview
       it belongs to is deleted. So it will be regenerated whenever there's a
       new publish or the CourseOverview schema version changes. It's not
       particularly smart about this, and will just re-write the same thumbnails
       over and over to the same location without checking to see if there were
       changes.

    4. Just because a CourseOverviewImageSet is successfully created does not
       mean that any thumbnails exist. There might have been a processing error,
       or there might simply be no source image to create a thumbnail out of.
       In this case, accessing CourseOverview.image_urls will return the value
       for course.course_image_url for all resolutions. CourseOverviewImageSet
       will *not* try to regenerate if there is a model entry with blank values
       for the URLs -- the assumption is that either there's no data there or
       something has gone wrong and needs fixing in code.

    5. If you want to change thumbnail resolutions, you need to create a new
       CourseOverviewImageConfig with the desired dimensions and then wipe the
       values in CourseOverviewImageSet.

    Logical next steps that I punted on for this first cut:

    1. Converting other parts of the app to use this.

       Our first cut only affects About Pages and the Student Dashboard. But
       most places that use course_image_url() should be converted -- e.g.
       course discovery, mobile, etc.

    2. Center cropping the image before scaling.

       This is desirable, but it involves a few edge cases (what the rounding
       policy is, what to do with undersized images, etc.) The behavior that
       we implemented is at least no worse than what was already there in terms
       of distorting images.

    3. Automatically invalidating entries based on CourseOverviewImageConfig.

       There are two basic paths I can think of for this. The first is to
       completely wipe this table when the config changes. The second is to
       actually tie the config as a foreign key from this model -- so you could
       do the comparison to see if the image_set's config_id matched
       CourseOverviewImageConfig.current() and invalidate it if they didn't
       match. I punted on this mostly because it's just not something that
       happens much at all in practice, there is an understood (if manual)
       process to do it, and it can happen in a follow-on PR if anyone is
       interested in extending this functionality.


    .. no_pii:
    """
    course_overview = models.OneToOneField(CourseOverview, db_index=True, related_name="image_set",
                                           on_delete=models.CASCADE)
    small_url = models.TextField(blank=True, default="")
    large_url = models.TextField(blank=True, default="")

    @classmethod
    def create(cls, course_overview, course=None):
        """
        Create thumbnail images for this CourseOverview.

        This will save the CourseOverviewImageSet before it returns.
        """
        from openedx.core.lib.courses import create_course_image_thumbnail

        # If image thumbnails are not enabled, do nothing.
        config = CourseOverviewImageConfig.current()
        if not config.enabled:
            return

        # If a course object was provided, use that. Otherwise, pull it from
        # CourseOverview's course_id. This happens because sometimes we are
        # generated as part of the CourseOverview creation (course is available
        # and passed in), and sometimes the CourseOverview already exists.
        if not course:
            course = modulestore().get_course(course_overview.id)

        image_set = cls(course_overview=course_overview)

        if course.course_image:
            # Try to create a thumbnails of the course image. If this fails for any
            # reason (weird format, non-standard URL, etc.), the URLs will default
            # to being blank. No matter what happens, we don't want to bubble up
            # a 500 -- an image_set is always optional.
            try:
                image_set.small_url = create_course_image_thumbnail(course, config.small)
                image_set.large_url = create_course_image_thumbnail(course, config.large)
            except Exception:  # pylint: disable=broad-except
                log.exception(
                    "Could not create thumbnail for course %s with image %s (small=%s), (large=%s)",
                    course.id,
                    course.course_image,
                    config.small,
                    config.large
                )

        # Regardless of whether we created thumbnails or not, we need to save
        # this record before returning. If no thumbnails were created (there was
        # an error or the course has no source course_image), our url fields
        # just keep their blank defaults.
        try:
            with transaction.atomic():
                image_set.save()
                course_overview.image_set = image_set
        except (IntegrityError, ValueError):
            # In the event of a race condition that tries to save two image sets
            # to the same CourseOverview, we'll just silently pass on the one
            # that fails. They should be the same data anyway.
            #
            # The ValueError above is to catch the following error that can
            # happen in Django 1.8.4+ if the CourseOverview object fails to save
            # (again, due to race condition).
            #
            # Example: ValueError: save() prohibited to prevent data loss due
            #          to unsaved related object 'course_overview'.")
            pass

    def __str__(self):
        return "CourseOverviewImageSet({}, small_url={}, large_url={})".format(
            self.course_overview_id, self.small_url, self.large_url
        )


class CourseOverviewImageConfig(ConfigurationModel):
    """
    This sets the size of the thumbnail images that Course Overviews will generate
    to display on the about, info, and student dashboard pages. If you make any
    changes to this, you will have to regenerate CourseOverviews in order for it
    to take effect. You might want to do this if you're doing precise theming of
    your install of edx-platform... but really, you probably don't want to do this
    at all at the moment, given how new this is. :-P

    .. no_pii:
    """
    # Small thumbnail, for things like the student dashboard
    small_width = models.IntegerField(default=375)
    small_height = models.IntegerField(default=200)

    # Large thumbnail, for things like the about page
    large_width = models.IntegerField(default=750)
    large_height = models.IntegerField(default=400)

    @property
    def small(self):
        """Tuple for small image dimensions in pixels -- (width, height)"""
        return (self.small_width, self.small_height)

    @property
    def large(self):
        """Tuple for large image dimensions in pixels -- (width, height)"""
        return (self.large_width, self.large_height)

    def __str__(self):
        return "CourseOverviewImageConfig(enabled={}, small={}, large={})".format(
            self.enabled, self.small, self.large
        )


class SimulateCoursePublishConfig(ConfigurationModel):
    """
    Manages configuration for a run of the simulate_publish management command.

    .. no_pii:
    """

    class Meta:
        app_label = 'course_overviews'
        verbose_name = 'simulate_publish argument'

    arguments = models.TextField(
        blank=True,
        help_text='Useful for manually running a Jenkins job. Specify like "--delay 10 --receivers A B C \
        --courses X Y Z".',
        default='',
    )

    def __str__(self):
        return str(self.arguments)


def _invalidate_overview_cache(**kwargs):  # pylint: disable=unused-argument
    """
    Invalidate the course overview request cache.
    """
    RequestCache('course_overview').clear()


post_save.connect(_invalidate_overview_cache, sender=CourseOverview)
post_save.connect(_invalidate_overview_cache, sender=CourseOverviewImageConfig)
post_delete.connect(_invalidate_overview_cache, sender=CourseOverview)
post_delete.connect(_invalidate_overview_cache, sender=CourseOverviewImageConfig)
