"""Helper functions for working with Programs."""

import datetime
import logging
from collections import defaultdict
from copy import deepcopy
from itertools import chain
from urllib.parse import urljoin, urlparse, urlunparse

from dateutil.parser import parse
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.urls import reverse
from django.utils.functional import cached_property
from opaque_keys.edx.keys import CourseKey
from zoneinfo import ZoneInfo
from requests.exceptions import RequestException

from common.djangoapps.course_modes.api import get_paid_modes_for_course
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.entitlements.api import get_active_entitlement_list_for_user
from common.djangoapps.entitlements.models import CourseEntitlement
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.util.date_utils import strftime_localized
from lms.djangoapps.certificates import api as certificate_api
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.commerce.utils import EcommerceService, get_program_price_info
from openedx.core.djangoapps.catalog.api import get_programs_by_type
from openedx.core.djangoapps.catalog.constants import PathwayType
from openedx.core.djangoapps.catalog.utils import (
    get_fulfillable_course_runs_for_entitlement,
    get_pathways,
    get_programs,
)
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.credentials.utils import get_credentials, get_credentials_records_url
from openedx.core.djangoapps.enrollments.api import get_enrollments
from openedx.core.djangoapps.enrollments.permissions import ENROLL_IN_COURSE
from openedx.core.djangoapps.programs import ALWAYS_CALCULATE_PROGRAM_PRICE_AS_ANONYMOUS_USER
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from xmodule.modulestore.django import modulestore

# The datetime module's strftime() methods require a year >= 1900.
DEFAULT_ENROLLMENT_START_DATE = datetime.datetime(1900, 1, 1, tzinfo=ZoneInfo("UTC"))

log = logging.getLogger(__name__)


def get_program_and_course_data(site, user, program_uuid, mobile_only=False):
    """Returns program and course data associated with the given user."""
    course_data = {}
    meter = ProgramProgressMeter(site, user, uuid=program_uuid)
    program_data = meter.programs[0]
    if program_data:
        program_data = ProgramDataExtender(program_data, user, mobile_only=mobile_only).extend()
        course_data = meter.progress(programs=[program_data], count_only=False)[0]
    return program_data, course_data


def get_program_urls(program_data):
    """Returns important urls of program."""
    from lms.djangoapps.learner_dashboard.utils import FAKE_COURSE_KEY, strip_course_id

    program_uuid = program_data.get("uuid")
    skus = program_data.get("skus")
    ecommerce_service = EcommerceService()

    # TODO: Don't have business logic of course-certificate==record-available here in LMS.
    # Eventually, the UI should ask Credentials if there is a record available and get a URL from it.
    # But this is here for now so that we can gate this URL behind both this business logic and
    # a waffle flag. This feature is in active developoment.
    program_record_url = get_credentials_records_url(program_uuid=program_uuid)
    urls = {
        "program_listing_url": reverse("program_listing_view"),
        "track_selection_url": strip_course_id(reverse("course_modes_choose", kwargs={"course_id": FAKE_COURSE_KEY})),
        "commerce_api_url": reverse("commerce_api:v0:baskets:create"),
        "buy_button_url": ecommerce_service.get_checkout_page_url(*skus),
        "program_record_url": program_record_url,
    }
    return urls


def get_industry_and_credit_pathways(program_data, site):
    """Returns pathways of a program."""
    industry_pathways = []
    credit_pathways = []
    try:
        for pathway_id in program_data["pathway_ids"]:
            pathway = get_pathways(site, pathway_id)
            if pathway and pathway["email"]:
                if pathway["pathway_type"] == PathwayType.CREDIT.value:
                    credit_pathways.append(pathway)
                elif pathway["pathway_type"] == PathwayType.INDUSTRY.value:
                    industry_pathways.append(pathway)
    # if pathway caching did not complete fully (no pathway_ids)
    except KeyError:
        pass

    return industry_pathways, credit_pathways


def get_program_marketing_url(programs_config, mobile_only=False):
    """Build a URL used to link to programs on the marketing site."""
    if mobile_only:
        marketing_url = "edxapp://course?programs"
    else:
        marketing_url = urljoin(settings.MKTG_URLS.get("ROOT"), programs_config.marketing_path).rstrip("/")

    return marketing_url


def attach_program_detail_url(programs, mobile_only=False):
    """Extend program representations by attaching a URL to be used when linking to program details.

    Facilitates the building of context to be passed to templates containing program data.

    Arguments:
        programs (list): Containing dicts representing programs.

    Returns:
        list, containing extended program dicts
    """
    for program in programs:
        if mobile_only:
            detail_fragment_url = reverse("program_details_fragment_view", kwargs={"program_uuid": program["uuid"]})
            path_id = detail_fragment_url.replace("/dashboard/", "")
            detail_url = f"edxapp://enrolled_program_info?path_id={path_id}"
        else:
            detail_url = reverse("program_details_view", kwargs={"program_uuid": program["uuid"]})

        program["detail_url"] = detail_url

    return programs


class ProgramProgressMeter:
    """Utility for gauging a user's progress towards program completion.

    Arguments:
        user (User): The user for which to find programs.

    Keyword Arguments:
        enrollments (list): List of the user's enrollments.
        uuid (str): UUID identifying a specific program. If provided, the meter
            will only inspect this one program, not all programs the user may be
            engaged with.
    """

    def __init__(self, site, user, enrollments=None, uuid=None, mobile_only=False, include_course_entitlements=True):
        self.site = site
        self.user = user
        self.mobile_only = mobile_only

        self.enrollments = enrollments or list(CourseEnrollment.enrollments_for_user(self.user))
        self.enrollments.sort(key=lambda e: e.created, reverse=True)

        self.enrolled_run_modes = {}
        self.course_run_ids = []
        for enrollment in self.enrollments:
            # enrollment.course_id is really a CourseKey (╯ಠ_ಠ）╯︵ ┻━┻
            enrollment_id = str(enrollment.course_id)
            mode = enrollment.mode
            if mode == CourseMode.NO_ID_PROFESSIONAL_MODE:
                mode = CourseMode.PROFESSIONAL
            self.enrolled_run_modes[enrollment_id] = mode
            # We can't use dict.keys() for this because the course run ids need to be ordered
            self.course_run_ids.append(enrollment_id)

        self.course_uuids = []
        if include_course_entitlements:
            self.entitlements = list(CourseEntitlement.unexpired_entitlements_for_user(self.user))
            self.course_uuids = [str(entitlement.course_uuid) for entitlement in self.entitlements]

        if uuid:
            self.programs = [get_programs(uuid=uuid)]
        else:
            self.programs = attach_program_detail_url(get_programs(self.site), self.mobile_only)

    def invert_programs(self):
        """Intersect programs and enrollments.

        Builds a dictionary of program dict lists keyed by course run ID and by course UUID.
        The resulting dictionary is suitable in applications where programs must be
        filtered by the course runs or courses they contain (e.g., the student dashboard).

        Returns:
            defaultdict, programs keyed by course run ID
        """
        inverted_programs = defaultdict(list)

        for program in self.programs:
            for course in program["courses"]:
                course_uuid = course["uuid"]
                if course_uuid in self.course_uuids:
                    program_list = inverted_programs[course_uuid]
                    if program not in program_list:
                        program_list.append(program)
                for course_run in course["course_runs"]:
                    course_run_id = course_run["key"]
                    if course_run_id in self.course_run_ids:
                        program_list = inverted_programs[course_run_id]
                        if program not in program_list:
                            program_list.append(program)

        # Sort programs by title for consistent presentation.
        for program_list in inverted_programs.values():
            program_list.sort(key=lambda p: p["title"])

        return inverted_programs

    @cached_property
    def engaged_programs(self) -> list[dict | None]:
        """Derive a list of programs in which the given user is engaged.

        Returns:
            list of program dicts, ordered by most recent enrollment
        """
        inverted_programs = self.invert_programs()

        programs = []
        # Remember that these course run ids are derived from a list of
        # enrollments sorted from most recent to least recent. Iterating
        # over the values in inverted_programs alone won't yield a program
        # ordering consistent with the user's enrollments.
        for course_run_id in self.course_run_ids:
            for program in inverted_programs[course_run_id]:
                # Dicts aren't a hashable type, so we can't use a set. Sets also
                # aren't ordered, which is important here.
                if program not in programs:
                    programs.append(program)

        for course_uuid in self.course_uuids:
            for program in inverted_programs[course_uuid]:
                if program not in programs:
                    programs.append(program)

        return programs

    def _is_course_in_progress(self, now, course):
        """Check if course qualifies as in progress as part of the program.

        A course is considered to be in progress if a user is enrolled in a run
        of the correct mode or a run of the correct mode is still available for enrollment.

        Arguments:
            now (datetime): datetime for now
            course (dict): Containing nested course runs.

        Returns:
            bool, indicating whether the course is in progress.
        """
        enrolled_runs = [run for run in course["course_runs"] if run["key"] in self.course_run_ids]

        # Check if the user is enrolled in a required run and mode/seat.
        runs_with_required_mode = [run for run in enrolled_runs if run["type"] == self.enrolled_run_modes[run["key"]]]

        if runs_with_required_mode:
            not_failed_runs = [run for run in runs_with_required_mode if run["key"] not in self.failed_course_runs]
            if not_failed_runs:
                return True

        # Check if seats required for course completion are still available.
        upgrade_deadlines = []
        for run in enrolled_runs:
            for seat in run["seats"]:
                if seat["type"] == run["type"] and run["type"] != self.enrolled_run_modes[run["key"]]:
                    upgrade_deadlines.append(seat["upgrade_deadline"])

        # An upgrade deadline of None means the course is always upgradeable.
        return any(not deadline or deadline and parse(deadline) > now for deadline in upgrade_deadlines)

    def progress(self, programs: list[dict | None] | None = None, count_only: bool = True) -> list[dict | None]:
        """Gauge a user's progress towards program completion.

        Keyword Arguments:
            programs (list): Specific list of programs to check the user's progress
                against. If left unspecified, self.engaged_programs will be used.

            count_only (bool): Whether or not to return counts of completed, in
                progress, and unstarted courses instead of serialized representations
                of the courses.

        Returns:
            list of dict, each containing information about a user's progress
                towards completing a program.
        """
        now = datetime.datetime.now(ZoneInfo("UTC"))

        progress = []
        programs = programs or self.engaged_programs
        for program in programs:
            program_copy = deepcopy(program)
            completed, in_progress, not_started = [], [], []

            for course in program_copy["courses"]:
                active_entitlement = CourseEntitlement.get_entitlement_if_active(
                    user=self.user, course_uuid=course["uuid"]
                )
                if self._is_course_complete(course):
                    completed.append(course)
                elif self._is_course_enrolled(course) or active_entitlement:
                    # Show all currently enrolled courses and active entitlements as in progress
                    if active_entitlement:
                        course["course_runs"] = get_fulfillable_course_runs_for_entitlement(
                            active_entitlement, course["course_runs"]
                        )
                        course["user_entitlement"] = active_entitlement.to_dict()
                        course["enroll_url"] = reverse(
                            "entitlements_api:v1:enrollments", args=[str(active_entitlement.uuid)]
                        )
                        in_progress.append(course)
                    else:
                        course_in_progress = self._is_course_in_progress(now, course)
                        if course_in_progress:
                            in_progress.append(course)
                        else:
                            course["expired"] = not course_in_progress
                            not_started.append(course)
                else:
                    not_started.append(course)

            progress.append(
                {
                    "uuid": program_copy["uuid"],
                    "completed": len(completed) if count_only else completed,
                    "in_progress": len(in_progress) if count_only else in_progress,
                    "not_started": len(not_started) if count_only else not_started,
                    "all_unenrolled": all(not self._is_course_enrolled(course) for course in program_copy["courses"]),
                }
            )

        return progress

    @property
    def completed_programs_with_available_dates(self):
        """
        Calculate the available date for completed programs based on course runs.

        Returns a dict of {uuid_string: available_datetime}
        """
        # Query for all user certs up front, for performance reasons (rather than querying per course run).
        user_certificates = certificate_api.get_eligible_and_available_certificates(user=self.user)
        certificates_by_run = {cert.course_id: cert for cert in user_certificates}

        completed = {}
        for program in self.programs:
            available_date = self._available_date_for_program(program, certificates_by_run)
            if available_date:
                completed[program["uuid"]] = available_date
        return completed

    def _available_date_for_program(self, program_data, certificates):
        """
        Calculate the available date for the program based on the courses within it.

        Arguments:
            program_data (dict): nested courses and course runs
            certificates (dict): course run key -> certificate mapping

        Returns a datetime object or None if the program is not complete.
        """
        program_available_date = None
        for course in program_data["courses"]:
            earliest_course_run_date = None

            for course_run in course["course_runs"]:
                key = CourseKey.from_string(course_run["key"])

                # Get a certificate if one exists
                certificate = certificates.get(key)
                if certificate is None:
                    continue

                # Modes must match (see _is_course_complete() comments for why)
                course_run_mode = self._course_run_mode_translation(course_run["type"])
                certificate_mode = self._certificate_mode_translation(certificate.mode)
                modes_match = course_run_mode == certificate_mode

                # Grab the available date and keep it if it's the earliest one for this catalog course.
                if modes_match and CertificateStatuses.is_passing_status(certificate.status):
                    course_overview = CourseOverview.get_from_id(key)
                    available_date = certificate_api.available_date_for_certificate(course_overview, certificate)
                    earliest_course_run_date = min(date for date in [available_date, earliest_course_run_date] if date)

            # If we're missing a cert for a course, the program isn't completed and we should just bail now
            if earliest_course_run_date is None:
                return None

            # Keep the catalog course date if it's the latest one
            program_available_date = max(date for date in [earliest_course_run_date, program_available_date] if date)

        return program_available_date

    def _course_run_mode_translation(self, course_run_mode):
        """
        Returns a canonical mode for a course run (whose data is coming from the program cache).
        This mode must match the certificate mode to be counted as complete.
        """
        mappings = {
            # Runs of type 'credit' are counted as 'verified' since verified
            # certificates are earned when credit runs are completed. LEARNER-1274
            # tracks a cleaner way to do this using the discovery service's
            # applicable_seat_types field.
            CourseMode.CREDIT_MODE: CourseMode.VERIFIED,
        }
        return mappings.get(course_run_mode, course_run_mode)

    def _certificate_mode_translation(self, certificate_mode):
        """
        Returns a canonical mode for a certificate (whose data is coming from the database).
        This mode must match the course run mode to be counted as complete.
        """
        mappings = {
            # Treat "no-id-professional" certificates as "professional" certificates
            CourseMode.NO_ID_PROFESSIONAL_MODE: CourseMode.PROFESSIONAL,
        }
        return mappings.get(certificate_mode, certificate_mode)

    def _is_course_complete(self, course):
        """Check if a user has completed a course.

        A course is completed if the user has earned a certificate for any of
        the nested course runs.

        Arguments:
            course (dict): Containing nested course runs.

        Returns:
            bool, indicating whether the course is complete.
        """

        def reshape(course_run):
            """
            Modify the structure of a course run dict to facilitate comparison
            with course run certificates.
            """
            return {
                "course_run_id": course_run["key"],
                # A course run's type is assumed to indicate which mode must be
                # completed in order for the run to count towards program completion.
                # This supports the same flexible program construction allowed by the
                # old programs service (e.g., completion of an old honor-only run may
                # count towards completion of a course in a program). This may change
                # in the future to make use of the more rigid set of "applicable seat
                # types" associated with each program type in the catalog.
                "type": self._course_run_mode_translation(course_run["type"]),
            }

        return any(reshape(course_run) in self.completed_course_runs for course_run in course["course_runs"])

    @cached_property
    def completed_course_runs(self):
        """
        Determine which course runs have been completed by the user.

        Returns:
            list of dicts, each representing a course run certificate
        """
        return self.course_runs_with_state["completed"]

    @cached_property
    def failed_course_runs(self):
        """
        Determine which course runs have been failed by the user.

        Returns:
            list of strings, each a course run ID
        """
        return [run["course_run_id"] for run in self.course_runs_with_state["failed"]]

    @cached_property
    def course_runs_with_state(self):
        """
        Determine which course runs have been completed and failed by the user.

        A course run is considered completed for a user if they have a certificate in the correct state and
        the certificate is available.

        Returns:
            dict with a list of completed and failed runs
        """
        course_run_certificates = certificate_api.get_certificates_for_user(self.user.username)

        completed_runs, failed_runs = [], []
        for certificate in course_run_certificates:
            course_key = certificate["course_key"]
            course_data = {
                "course_run_id": str(course_key),
                "type": self._certificate_mode_translation(certificate["type"]),
            }

            try:
                course_overview = CourseOverview.get_from_id(course_key)
            except CourseOverview.DoesNotExist:
                may_certify = True
            else:
                may_certify = certificate_api.certificates_viewable_for_course(course_overview)

            if CertificateStatuses.is_passing_status(certificate["status"]) and may_certify:
                completed_runs.append(course_data)
            else:
                failed_runs.append(course_data)

        return {"completed": completed_runs, "failed": failed_runs}

    def _is_course_enrolled(self, course):
        """Check if a user is enrolled in a course.

        A user is considered to be enrolled in a course if
        they're enrolled in any of the nested course runs.

        Arguments:
            course (dict): Containing nested course runs.

        Returns:
            bool, indicating whether the course is in progress.
        """
        return any(course_run["key"] in self.course_run_ids for course_run in course["course_runs"])


# pylint: disable=missing-docstring
class ProgramDataExtender:
    """
    Utility for extending program data meant for the program detail page with
    user-specific (e.g., CourseEnrollment) data.

    Arguments:
        program_data (dict): Representation of a program.
        user (User): The user whose enrollments to inspect.
    """

    def __init__(self, program_data, user, mobile_only=False):
        self.data = program_data
        self.user = user
        self.mobile_only = mobile_only
        self.data.update({"is_mobile_only": self.mobile_only})

        self.course_run_key = None
        self.course_overview = None
        self.enrollment_start = None

    def extend(self):
        """Execute extension handlers, returning the extended data."""
        self._execute("_extend")
        self._collect_one_click_purchase_eligibility_data()
        return self.data

    def _execute(self, prefix, *args):
        """Call handlers whose name begins with the given prefix with the given arguments."""
        [getattr(self, handler)(*args) for handler in self._handlers(prefix)]  # pylint: disable=expression-not-assigned

    @classmethod
    def _handlers(cls, prefix):
        """Returns a generator yielding method names beginning with the given prefix."""
        return (name for name in cls.__dict__ if name.startswith(prefix))

    def _extend_course_runs(self):
        """Execute course run data handlers."""
        for course in self.data["courses"]:
            for course_run in course["course_runs"]:
                # State to be shared across handlers.
                self.course_run_key = CourseKey.from_string(course_run["key"])

                # Some (old) course runs may exist for a program which do not exist in LMS. In that case,
                # continue without the course run.
                try:
                    self.course_overview = CourseOverview.get_from_id(self.course_run_key)
                except CourseOverview.DoesNotExist:
                    log.warning("Failed to get course overview for course run key: %s", course_run.get("key"))
                else:
                    self.enrollment_start = self.course_overview.enrollment_start or DEFAULT_ENROLLMENT_START_DATE

                    self._execute("_attach_course_run", course_run)

    def _attach_course_run_certificate_url(self, run_mode):
        certificate_data = certificate_api.certificate_downloadable_status(self.user, self.course_run_key)
        certificate_uuid = certificate_data.get("uuid")
        run_mode["certificate_url"] = (
            certificate_api.get_certificate_url(
                user_id=self.user.id,  # Providing user_id allows us to fall back to PDF certificates
                # if web certificates are not configured for a given course.
                course_id=self.course_run_key,
                uuid=certificate_uuid,
            )
            if certificate_uuid
            else None
        )

    def _attach_course_run_course_url(self, run_mode):
        if self.mobile_only:
            run_mode["course_url"] = "edxapp://enrolled_course_info?course_id={}".format(run_mode.get("key"))
        else:
            run_mode["course_url"] = reverse("course_root", args=[self.course_run_key])

    def _attach_course_run_enrollment_open_date(self, run_mode):
        run_mode["enrollment_open_date"] = strftime_localized(self.enrollment_start, "SHORT_DATE")

    def _attach_course_run_is_course_ended(self, run_mode):
        end_date = self.course_overview.end or datetime.datetime.max.replace(tzinfo=ZoneInfo("UTC"))
        run_mode["is_course_ended"] = end_date < datetime.datetime.now(ZoneInfo("UTC"))

    def _attach_course_run_is_enrolled(self, run_mode):
        run_mode["is_enrolled"] = CourseEnrollment.is_enrolled(self.user, self.course_run_key)

    def _attach_course_run_is_enrollment_open(self, run_mode):
        enrollment_end = self.course_overview.enrollment_end or datetime.datetime.max.replace(tzinfo=ZoneInfo("UTC"))
        run_mode["is_enrollment_open"] = (
            self.enrollment_start <= datetime.datetime.now(ZoneInfo("UTC")) < enrollment_end
        )

    def _attach_course_run_advertised_start(self, run_mode):
        """
        The advertised_start is text a course author can provide to be displayed
        instead of their course's start date. For example, if a course run were
        to start on December 1, 2016, the author might provide 'Winter 2016' as
        the advertised start.
        """
        run_mode["advertised_start"] = self.course_overview.advertised_start

    def _attach_course_run_upgrade_url(self, run_mode):
        required_mode_slug = run_mode["type"]
        enrolled_mode_slug, _ = CourseEnrollment.enrollment_mode_for_user(self.user, self.course_run_key)
        is_mode_mismatch = required_mode_slug != enrolled_mode_slug
        is_upgrade_required = is_mode_mismatch and CourseEnrollment.is_enrolled(self.user, self.course_run_key)

        if is_upgrade_required:
            # Requires that the ecommerce service be in use.
            required_mode = CourseMode.mode_for_course(self.course_run_key, required_mode_slug)
            ecommerce = EcommerceService()
            sku = getattr(required_mode, "sku", None)
            if ecommerce.is_enabled(self.user) and sku:
                run_mode["upgrade_url"] = ecommerce.get_checkout_page_url(
                    required_mode.sku, course_run_keys=[self.course_run_key]
                )
            else:
                run_mode["upgrade_url"] = None
        else:
            run_mode["upgrade_url"] = None

    def _attach_course_run_may_certify(self, run_mode):
        run_mode["may_certify"] = certificate_api.certificates_viewable_for_course(self.course_overview)

    def _attach_course_run_is_mobile_only(self, run_mode):
        run_mode["is_mobile_only"] = self.mobile_only

    def _filter_out_courses_with_entitlements(self, courses):
        """
        Removes courses for which the current user already holds an applicable entitlement.

        TODO:
            Add a NULL value of enrollment_course_run to filter, as courses with entitlements spent on applicable
            enrollments will already have been filtered out by _filter_out_courses_with_enrollments.

        Arguments:
            courses (list): Containing dicts representing courses in a program

        Returns:
            A subset of the given list of course dicts
        """
        course_uuids = {course["uuid"] for course in courses}
        # Filter the entitlements' modes with a case-insensitive match against applicable seat_types
        entitlements = self.user.courseentitlement_set.filter(
            mode__in=self.data["applicable_seat_types"],
            course_uuid__in=course_uuids,
        )
        # Here we check the entitlements' expired_at_datetime property rather than filter by the expired_at attribute
        # to ensure that the expiration status is as up to date as possible
        entitlements = [e for e in entitlements if not e.expired_at_datetime]
        courses_with_entitlements = {str(entitlement.course_uuid) for entitlement in entitlements}
        return [course for course in courses if course["uuid"] not in courses_with_entitlements]

    def _filter_out_courses_with_enrollments(self, courses):
        """
        Removes courses for which the current user already holds an active and applicable enrollment
        for one of that course's runs.

        Arguments:
            courses (list): Containing dicts representing courses in a program

        Returns:
            A subset of the given list of course dicts
        """
        enrollments = self.user.courseenrollment_set.filter(is_active=True, mode__in=self.data["applicable_seat_types"])
        course_runs_with_enrollments = {str(enrollment.course_id) for enrollment in enrollments}
        courses_without_enrollments = []
        for course in courses:
            if all(str(run["key"]) not in course_runs_with_enrollments for run in course["course_runs"]):
                courses_without_enrollments.append(course)

        return courses_without_enrollments

    def _collect_one_click_purchase_eligibility_data(self):  # lint-amnesty, pylint: disable=too-many-statements
        """
        Extend the program data with data about learner's eligibility for one click purchase,
        discount data of the program and SKUs of seats that should be added to basket.
        """
        if "professional" in self.data["applicable_seat_types"]:
            self.data["applicable_seat_types"].append("no-id-professional")
        applicable_seat_types = {seat for seat in self.data["applicable_seat_types"] if seat != "credit"}

        is_learner_eligible_for_one_click_purchase = self.data["is_program_eligible_for_one_click_purchase"]
        bundle_uuid = self.data.get("uuid")
        skus = []
        course_keys = []
        bundle_variant = "full"

        if is_learner_eligible_for_one_click_purchase:  # lint-amnesty, pylint: disable=too-many-nested-blocks
            courses = self.data["courses"]
            if not self.user.is_anonymous:
                courses = self._filter_out_courses_with_enrollments(courses)
                courses = self._filter_out_courses_with_entitlements(courses)

            if len(courses) < len(self.data["courses"]):
                bundle_variant = "partial"

            for course in courses:
                entitlement_product = False
                for entitlement in course.get("entitlements", []):
                    # We add the first entitlement product found with an applicable seat type because, at this time,
                    # we are assuming that, for any given course, there is at most one paid entitlement available.
                    if entitlement["mode"] in applicable_seat_types:
                        skus.append(entitlement["sku"])
                        course_keys.append(course.get("key"))
                        entitlement_product = True
                        break
                if not entitlement_product:
                    course_runs = course.get("course_runs", [])
                    published_course_runs = [run for run in course_runs if run["status"] == "published"]
                    if len(published_course_runs) == 1:
                        for seat in published_course_runs[0]["seats"]:
                            if seat["type"] in applicable_seat_types and seat["sku"]:
                                skus.append(seat["sku"])
                                course_keys.append(course.get("key"))
                                break
                    else:
                        # If a course in the program has more than 1 published course run
                        # learner won't be eligible for a one click purchase.
                        skus = []
                        break

        if skus:
            try:
                is_anonymous = not self.user.is_authenticated

                # The user specific program price is slow to calculate, so use switch to force the
                # anonymous price for all users. See LEARNER-5555 for more details.
                if is_anonymous or ALWAYS_CALCULATE_PROGRAM_PRICE_AS_ANONYMOUS_USER.is_enabled():
                    # The bundle uuid is necessary to see the program's discounted price
                    if bundle_uuid:
                        params = dict(sku=skus, is_anonymous=True, bundle=bundle_uuid, course_key=course_keys)
                    else:
                        params = dict(sku=skus, is_anonymous=True, course_key=course_keys)
                else:
                    if bundle_uuid:
                        params = dict(sku=skus, username=self.user.username, bundle=bundle_uuid, course_key=course_keys)
                    else:
                        params = dict(sku=skus, username=self.user.username, course_key=course_keys)

                response = get_program_price_info(self.user, params)
                response.raise_for_status()
                discount_data = response.json()
                program_discounted_price = discount_data["total_incl_tax"]
                program_full_price = discount_data["total_incl_tax_excl_discounts"]
                discount_data["is_discounted"] = program_discounted_price < program_full_price
                discount_data["discount_value"] = program_full_price - program_discounted_price

                self.data.update(
                    {
                        "discount_data": discount_data,
                        "full_program_price": discount_data["total_incl_tax"],
                        "variant": bundle_variant,
                    }
                )
            except RequestException:
                log.exception("Failed to get discount price for following product SKUs: %s ", ", ".join(skus))
                self.data.update({"discount_data": {"is_discounted": False}})
        else:
            is_learner_eligible_for_one_click_purchase = False

        self.data.update(
            {
                "is_learner_eligible_for_one_click_purchase": is_learner_eligible_for_one_click_purchase,
                "skus": skus,
            }
        )


def get_certificates(user, extended_program):
    """
    Find certificates a user has earned related to a given program.

    Arguments:
        user (User): The user whose enrollments to inspect.
        extended_program (dict): The program for which to locate certificates.
            This is expected to be an "extended" program whose course runs already
            have certificate URLs attached.

    Returns:
        list: Contains dicts representing course run and program certificates the
            given user has earned which are associated with the given program.
    """
    certificates = []

    for course in extended_program["courses"]:
        for course_run in course["course_runs"]:
            url = course_run.get("certificate_url")
            if url and course_run.get("may_certify"):
                certificates.append(
                    {
                        "type": "course",
                        "title": course_run["title"],
                        "url": url,
                    }
                )

                # We only want one certificate per course to be returned.
                break

    program_credentials = get_credentials(user, program_uuid=extended_program["uuid"], credential_type="program")
    # only include a program certificate if a certificate is available for every course
    if program_credentials and (len(certificates) == len(extended_program["courses"])):
        enabled_force_program_cert_auth = configuration_helpers.get_value("force_program_cert_auth", True)
        cert_url = program_credentials[0]["certificate_url"]
        url = get_logged_in_program_certificate_url(cert_url) if enabled_force_program_cert_auth else cert_url

        certificates.append(
            {
                "type": "program",
                "title": extended_program["title"],
                "url": url,
            }
        )

    return certificates


def get_logged_in_program_certificate_url(certificate_url):
    parsed_url = urlparse(certificate_url)
    query_string = "next=" + parsed_url.path
    url_parts = (parsed_url.scheme, parsed_url.netloc, "/login/", "", query_string, "")
    return urlunparse(url_parts)


class ProgramMarketingDataExtender(ProgramDataExtender):
    """
    Utility for extending program data meant for the program marketing page which lives in the
    edx-platform git repository with user-specific (e.g., CourseEnrollment) data, pricing data,
    and program instructor data.

    Arguments:
        program_data (dict): Representation of a program.
        user (User): The user whose enrollments to inspect.
    """

    def __init__(self, program_data, user):
        super().__init__(program_data, user)

        # Aggregate list of instructors for the program keyed by name
        self.instructors = []

        # Values for programs' price calculation.
        self.data["avg_price_per_course"] = 0.0
        self.data["number_of_courses"] = 0
        self.data["full_program_price"] = 0.0

    def _extend_program(self):
        """Aggregates data from the program data structure."""
        cache_key = "program.instructors.{uuid}".format(uuid=self.data["uuid"])
        program_instructors = cache.get(cache_key)

        for course in self.data["courses"]:
            self._execute("_collect_course", course)
            if not program_instructors:
                for course_run in course["course_runs"]:
                    self._execute("_collect_instructors", course_run)

        if not program_instructors:
            # We cache the program instructors list to avoid repeated modulestore queries
            program_instructors = self.instructors
            cache.set(cache_key, program_instructors, 3600)

        if "instructor_ordering" not in self.data:
            # If no instructor ordering is set in discovery, it doesn't populate this key
            self.data["instructor_ordering"] = []

        sorted_instructor_names = [
            " ".join([name for name in (instructor["given_name"], instructor["family_name"]) if name])
            for instructor in self.data["instructor_ordering"]
        ]
        instructors_to_be_sorted = [
            instructor for instructor in program_instructors if instructor["name"] in sorted_instructor_names
        ]
        instructors_to_not_be_sorted = [
            instructor for instructor in program_instructors if instructor["name"] not in sorted_instructor_names
        ]
        sorted_instructors = sorted(
            instructors_to_be_sorted, key=lambda item: sorted_instructor_names.index(item["name"])
        )
        self.data["instructors"] = sorted_instructors + instructors_to_not_be_sorted

    def extend(self):
        """Execute extension handlers, returning the extended data."""
        self.data.update(super().extend())
        return self.data

    @classmethod
    def _handlers(cls, prefix):
        """Returns a generator yielding method names beginning with the given prefix."""
        # We use a set comprehension here to deduplicate the list of
        # function names given the fact that the subclass overrides
        # some functions on the parent class.
        return {name for name in chain(cls.__dict__, ProgramDataExtender.__dict__) if name.startswith(prefix)}

    def _attach_course_run_can_enroll(self, run_mode):
        run_mode["can_enroll"] = bool(self.user.has_perm(ENROLL_IN_COURSE, self.course_overview))

    def _attach_course_run_certificate_url(self, run_mode):
        """
        We override this function here and stub it out because
        the superclass (ProgramDataExtender) requires a non-anonymous
        User which we may or may not have when rendering marketing
        pages. The certificate URL is not needed when rendering
        the program marketing page.
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    def _attach_course_run_upgrade_url(self, run_mode):
        if not self.user.is_anonymous:
            super()._attach_course_run_upgrade_url(run_mode)
        else:
            run_mode["upgrade_url"] = None

    def _collect_course_pricing(self, course):
        self.data["number_of_courses"] += 1
        course_runs = course["course_runs"]
        if course_runs:
            seats = course_runs[0]["seats"]
            if seats:
                self.data["full_program_price"] += float(seats[0]["price"])
            self.data["avg_price_per_course"] = self.data["full_program_price"] / self.data["number_of_courses"]

    def _collect_instructors(self, course_run):
        """
        Extend the program data with instructor data. The instructor data added here is persisted
        on each course in modulestore and can be edited in Studio. Once the course metadata publisher tool
        supports the authoring of course instructor data, we will be able to migrate course
        instructor data into the catalog, retrieve it via the catalog API, and remove this code.
        """
        module_store = modulestore()
        course_run_key = CourseKey.from_string(course_run["key"])
        course_block = module_store.get_course(course_run_key)
        if course_block:
            course_instructors = getattr(course_block, "instructor_info", {})

            # Deduplicate program instructors using instructor name
            curr_instructors_names = [instructor.get("name", "").strip() for instructor in self.instructors]
            for instructor in course_instructors.get("instructors", []):
                if instructor.get("name", "").strip() not in curr_instructors_names:
                    self.instructors.append(instructor)


def is_user_enrolled_in_program_type(
    user, program_type_slug, paid_modes_only=False, enrollments=None, entitlements=None
):  # lint-amnesty, pylint: disable=line-too-long
    """
    This method will look at the learners Enrollments and Entitlements to determine
    if a learner is enrolled in a Program of the given type.

    NOTE: This method relies on the Program Cache right now. The goal is to move away from this
    in the future.

    Arguments:
        user (User): The user we are looking for.
        program_type_slug (str): The slug of the Program type we are looking for.
        paid_modes_only (bool): Request if the user is enrolled in a Program in a paid mode, False by default.
        enrollments (List[Dict]): Takes a serialized list of CourseEnrollments linked to the user
        entitlements (List[CourseEntitlement]): Take a list of CourseEntitlement objects linked to the user

        NOTE: Both enrollments and entitlements will be collected if they are not passed in. They are available
        as parameters in case they were already collected, to save duplicate queries in high traffic areas.

    Returns:
        bool: True is the user is enrolled in programs of the requested type
    """
    course_runs = set()
    course_uuids = set()
    programs = get_programs_by_type(Site.objects.get_current(), program_type_slug)
    if not programs:
        return False

    for program in programs:
        for course in program.get("courses", []):
            course_uuids.add(course.get("uuid"))
            for course_run in course.get("course_runs", []):
                course_runs.add(course_run["key"])

    # Check Entitlements first, because there will be less Course Entitlements than
    # Course Run Enrollments.
    student_entitlements = entitlements if entitlements is not None else get_active_entitlement_list_for_user(user)
    for entitlement in student_entitlements:
        if str(entitlement.course_uuid) in course_uuids:
            return True

    student_enrollments = enrollments if enrollments is not None else get_enrollments(user.username)
    for enrollment in student_enrollments:
        course_run_id = enrollment["course_details"]["course_id"]
        if paid_modes_only:
            course_run_key = CourseKey.from_string(course_run_id)
            paid_modes = [mode.slug for mode in get_paid_modes_for_course(course_run_key)]
            if enrollment["mode"] in paid_modes and course_run_id in course_runs:
                return True
        elif course_run_id in course_runs:
            return True
    return False
