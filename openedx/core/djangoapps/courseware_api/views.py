"""
Course API Views
"""
from completion.exceptions import UnavailableCompletionData
from completion.utilities import get_key_to_last_completed_block
from django.conf import settings
from django.db import transaction
from django.utils.decorators import method_decorator
from django.utils.functional import cached_property
from edx_django_utils.cache import TieredCache
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError, NoPathToItem
from xmodule.modulestore.search import path_to_location
from xmodule.x_module import PUBLIC_VIEW, STUDENT_VIEW

from common.djangoapps.course_modes.models import CourseMode, get_course_prices
from common.djangoapps.util.views import expose_header
from lms.djangoapps.edxnotes.helpers import is_feature_enabled
from lms.djangoapps.certificates.api import get_certificate_url, get_eligible_certificate
from lms.djangoapps.commerce.utils import EcommerceService
from lms.djangoapps.course_api.api import course_detail
from lms.djangoapps.course_goals.models import UserActivity
from lms.djangoapps.course_goals.api import get_course_goal
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.access_utils import check_public_access
from lms.djangoapps.courseware.courses import (
    get_course_about_section,
    get_course_with_access,
    get_permission_for_course_about,
)

from lms.djangoapps.courseware.context_processor import user_timezone_locale_prefs
from lms.djangoapps.courseware.entrance_exams import course_has_entrance_exam, user_has_passed_entrance_exam
from lms.djangoapps.courseware.masquerade import (
    is_masquerading_as_specific_student,
    setup_masquerade,
    is_masquerading_as_non_audit_enrollment,
)
from lms.djangoapps.courseware.models import LastSeenCoursewareTimezone
from lms.djangoapps.courseware.permissions import VIEW_COURSEWARE
from lms.djangoapps.courseware.block_render import get_block_by_usage_id
from lms.djangoapps.courseware.toggles import course_exit_page_is_active, course_is_invitation_only
from lms.djangoapps.courseware.views.views import get_cert_data
from lms.djangoapps.gating.api import get_entrance_exam_score, get_entrance_exam_usage_key
from lms.djangoapps.grades.api import CourseGradeFactory
from lms.djangoapps.instructor.enrollment import uses_shib
from common.djangoapps.util.milestones_helpers import get_prerequisite_courses_display
from lms.djangoapps.verify_student.services import IDVerificationService
from openedx.core.djangoapps.agreements.api import get_integrity_signature
from openedx.core.djangoapps.courseware_api.utils import get_celebrations_dict
from openedx.core.djangoapps.enrollments.permissions import ENROLL_IN_COURSE
from openedx.core.djangoapps.programs.utils import ProgramProgressMeter
from openedx.core.djangolib.markup import clean_dangerous_html
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin
from openedx.core.lib.courses import get_course_by_id
from openedx.features.course_experience import ENABLE_COURSE_GOALS
from openedx.features.course_experience.waffle import ENABLE_COURSE_ABOUT_SIDEBAR_HTML
from openedx.features.content_type_gating.models import ContentTypeGatingConfig
from openedx.features.course_duration_limits.access import get_access_expiration_data
from openedx.features.discounts.utils import generate_offer_data
from common.djangoapps.student.models import (
    CourseEnrollment,
    CourseEnrollmentCelebration,
    LinkedInAddToProfileConfiguration
)
from xmodule.course_block import (
    COURSE_VISIBILITY_PUBLIC,
    COURSE_VISIBILITY_PUBLIC_OUTLINE,
)

from .serializers import CourseInfoSerializer


class CoursewareMeta:
    """
    Encapsulates courseware and enrollment metadata.
    """

    def __init__(self, course_key, request, username=''):
        self.request = request
        self.course_overview = course_detail(
            self.request,
            username or self.request.user.username,
            course_key,
        )

        original_user_is_staff = has_access(self.request.user, 'staff', self.course_overview).has_access
        self.original_user_is_global_staff = self.request.user.is_staff
        self.course_key = course_key
        self.course = get_course_by_id(self.course_key)
        self.course_masquerade, self.effective_user = setup_masquerade(
            self.request,
            course_key,
            staff_access=original_user_is_staff,
        )
        self.request.user = self.effective_user
        self.course_overview.bind_course_for_student(self.request)
        self.enrollment_object = CourseEnrollment.get_enrollment(self.effective_user, self.course_key,
                                                                 select_related=['celebration', 'user__celebration'])
        self.ecomm_service = EcommerceService()

    def __getattr__(self, name):
        return getattr(self.course_overview, name)

    @property
    def enrollment(self):
        """
        Return enrollment information.
        """
        if self.effective_user.is_anonymous or not self.enrollment_object:
            mode = None
            is_active = False
        else:
            mode = self.enrollment_object.mode
            is_active = self.enrollment_object.is_active
        return {'mode': mode, 'is_active': is_active}

    @property
    def access_expiration(self):
        return get_access_expiration_data(self.effective_user, self.course_overview)

    @property
    def offer(self):
        return generate_offer_data(self.effective_user, self.course_overview)

    @property
    def content_type_gating_enabled(self):
        return ContentTypeGatingConfig.enabled_for_enrollment(
            user=self.effective_user,
            course_key=self.course_key,
        )

    @property
    def license(self):
        return self.course.license

    @property
    def language(self):
        return self.course.language

    @property
    def notes(self):
        """
        Return whether edxnotes is enabled and visible.
        """
        return {
            'enabled': is_feature_enabled(self.course_overview, self.effective_user),
            'visible': self.course_overview.edxnotes_visibility,
        }

    @property
    def celebrations(self):
        """
        Returns a dict of celebrations that should be performed.
        """
        browser_timezone = self.request.query_params.get('browser_timezone', None)
        celebrations = get_celebrations_dict(self.effective_user, self.enrollment_object, self.course, browser_timezone)
        return celebrations

    @property
    def course_goals(self):
        """
        Returns a dict of course goals
        """
        course_goals = {
            'selected_goal': None,
            'weekly_learning_goal_enabled': False,
        }
        user_is_enrolled = CourseEnrollment.is_enrolled(self.effective_user, self.course_key)
        if (user_is_enrolled and ENABLE_COURSE_GOALS.is_enabled(self.course_key)):
            course_goals['weekly_learning_goal_enabled'] = True
            selected_goal = get_course_goal(self.effective_user, self.course_key)
            if selected_goal:
                course_goals['selected_goal'] = {
                    'days_per_week': selected_goal.days_per_week,
                    'subscribed_to_reminders': selected_goal.subscribed_to_reminders,
                }
        return course_goals

    @cached_property
    def course_grade(self):
        """
        Returns the Course Grade for the effective user in the course

        Cached property since we use this twice in the class and don't want to recreate the entire grade.
        """
        return CourseGradeFactory().read(self.effective_user, self.course)

    @property
    def user_has_passing_grade(self):
        """ Returns a boolean on if the effective_user has a passing grade in the course """
        if not self.effective_user.is_anonymous:
            user_grade = self.course_grade.percent
            return user_grade >= self.course.lowest_passing_grade
        return False

    @property
    def course_exit_page_is_active(self):
        """ Returns a boolean on if the course exit page is active """
        return course_exit_page_is_active(self.course_key)

    @property
    def certificate_data(self):
        """
        Returns certificate data if the effective_user is enrolled.
        Note: certificate data can be None depending on learner and/or course state.
        """
        if self.enrollment_object:
            return get_cert_data(self.effective_user, self.course, self.enrollment_object.mode)

    @property
    def entrance_exam_data(self):
        """
        Returns Entrance Exam data for the course

        Although some of the fields will have values (i.e. entrance_exam_minimum_score_pct and
        entrance_exam_passed), nothing will be used unless entrance_exam_enabled is True.
        """
        return {
            'entrance_exam_current_score': get_entrance_exam_score(
                self.course_grade, get_entrance_exam_usage_key(self.course_overview),
            ),
            'entrance_exam_enabled': course_has_entrance_exam(self.course_overview),
            'entrance_exam_id': self.course_overview.entrance_exam_id,
            'entrance_exam_minimum_score_pct': self.course_overview.entrance_exam_minimum_score_pct,
            'entrance_exam_passed': user_has_passed_entrance_exam(self.effective_user, self.course_overview),
        }

    @property
    def verify_identity_url(self):
        """
        Returns a String to the location to verify a learner's identity
        Note: This might return an absolute URL (if the verification MFE is enabled) or a relative
            URL. The serializer will make the relative URL absolute so any consumers can treat this
            as a full URL.
        """
        if self.enrollment_object and self.enrollment_object.mode in CourseMode.VERIFIED_MODES:
            verification_status = IDVerificationService.user_status(self.effective_user)['status']
            if verification_status == 'must_reverify':
                return IDVerificationService.get_verify_location()
            else:
                return IDVerificationService.get_verify_location(self.course_key)

    @property
    def verification_status(self):
        """
        Returns a String of the verification status of learner.
        """
        if self.enrollment_object and self.enrollment_object.mode in CourseMode.VERIFIED_MODES:
            return IDVerificationService.user_status(self.effective_user)['status']
        # I know this looks weird (and is), but this is just so it is inline with what the
        # IDVerificationService.user_status method would return before a verification was created
        return 'none'

    @property
    def linkedin_add_to_profile_url(self):
        """
        Returns a URL to add a certificate to a LinkedIn profile (will autofill fields).

        Requires LinkedIn sharing to be enabled, either via a site configuration or a
        LinkedInAddToProfileConfiguration object being enabled.
        """
        if self.effective_user.is_anonymous:
            return

        linkedin_config = LinkedInAddToProfileConfiguration.current()
        if linkedin_config.is_enabled():

            user_certificate = get_eligible_certificate(user=self.effective_user, course_id=self.course_key)

            if user_certificate is None:
                return

            cert_url = self.request.build_absolute_uri(
                get_certificate_url(course_id=self.course_key, uuid=user_certificate.verify_uuid)
            )
            return linkedin_config.add_to_profile_url(
                self.course_overview, user_certificate.mode, cert_url, certificate=user_certificate,
            )

    @property
    def is_integrity_signature_enabled(self):
        """
        Django setting for the integrity signature feature.
        """
        return settings.FEATURES.get('ENABLE_INTEGRITY_SIGNATURE', False)

    @property
    def user_needs_integrity_signature(self):
        """
        Boolean describing whether the user needs to sign the integrity agreement for a course.
        """
        if not settings.FEATURES.get('ENABLE_INTEGRITY_SIGNATURE'):
            return False

        integrity_signature_required = (
            self.enrollment_object
            # Master's enrollments are excluded here as honor code is handled separately
            and self.enrollment_object.mode in CourseMode.CREDIT_MODES + CourseMode.CREDIT_ELIGIBLE_MODES
        )

        if not integrity_signature_required:
            # Check masquerading as a non-audit enrollment
            integrity_signature_required = is_masquerading_as_non_audit_enrollment(
                self.effective_user,
                self.course_key,
                self.course_masquerade
            )

        if integrity_signature_required:
            signature = get_integrity_signature(self.effective_user.username, str(self.course_key))
            if not signature:
                return True

        return False

    @property
    def related_programs(self):
        """
        Returns related program data if the effective_user is enrolled.
        Note: related programs can be None depending on the course.
        """
        if self.effective_user.is_anonymous:
            return

        meter = ProgramProgressMeter(self.request.site, self.effective_user)
        inverted_programs = meter.invert_programs()
        related_programs = inverted_programs.get(str(self.course_key))

        if related_programs is None:
            return

        related_progress = meter.progress(programs=related_programs)
        progress_by_program = {
            progress['uuid']: progress for progress in related_progress
        }

        programs = [{
            'progress': progress_by_program[program['uuid']],
            'title': program['title'],
            'slug': program['type_attrs']['slug'],
            'url': program['detail_url'],
            'uuid': program['uuid']
        } for program in related_programs]

        return programs

    @property
    def user_timezone(self):
        """Returns the user's timezone setting (may be None)"""
        user_timezone_locale = user_timezone_locale_prefs(self.request)
        return user_timezone_locale['user_timezone']

    @property
    def can_access_proctored_exams(self):
        """Returns if the user is eligible to access proctored exams"""
        if is_masquerading_as_non_audit_enrollment(
            self.effective_user,
            self.course_key,
            self.course_masquerade
        ):
            # Masquerading should mimic the correct enrollment track behavior.
            return True
        else:
            enrollment_mode = self.enrollment['mode']
            enrollment_active = self.enrollment['is_active']
            return enrollment_active and CourseMode.is_eligible_for_certificate(enrollment_mode)

    @property
    def learning_assistant_enabled(self):
        """
        Returns a boolean representing whether the requesting user should have access to the Xpert Learning Assistant.
        """
        return getattr(settings, 'LEARNING_ASSISTANT_AVAILABLE', False)

    @property
    def show_courseware_link(self):
        """
        Returns a boolean representing whether the courseware link should be shown in the course details page.
        """
        with modulestore().bulk_operations(self.course_key):
            permission = get_permission_for_course_about()
            course_with_access = get_course_with_access(self.request.user, permission, self.course_key)
            return bool(
                self.request.user.has_perm(VIEW_COURSEWARE, course_with_access)
                or settings.FEATURES.get('ENABLE_LMS_MIGRATION')
            )

    @property
    def is_course_full(self):
        """
        Returns a boolean representing whether the course is full.
        """
        return CourseEnrollment.objects.is_course_full(self.course)

    @property
    def can_enroll(self):
        """
        Returns a boolean representing whether the user can enroll in the course.
        """
        return bool(self.request.user.has_perm(ENROLL_IN_COURSE, self.course))

    @property
    def invitation_only(self):
        """
        Returns a boolean representing whether the course is invitation only.
        """
        return course_is_invitation_only(self.course)

    @property
    def is_shib_course(self):
        """
        Returns a boolean representing whether the course is a Shibboleth course.
        """
        return uses_shib(self.course)

    @property
    def allow_anonymous(self):
        """
        Returns a boolean representing whether the course allows anonymous access.
        """
        return bool(check_public_access(self.course, [COURSE_VISIBILITY_PUBLIC, COURSE_VISIBILITY_PUBLIC_OUTLINE]))

    @property
    def ecommerce_checkout(self):
        """
        Returns a boolean representing whether the course has an ecommerce checkout.
        """
        return self.ecomm_service.is_enabled(self.request.user)

    @property
    def single_paid_mode(self):
        """
        Returns a dict representing the single paid mode for the course, if it exists.
        """
        modes = CourseMode.modes_for_course_dict(self.course_key)
        single_paid_mode = {}
        if self.ecommerce_checkout:
            if len(modes) == 1 and list(modes.values())[0].min_price:
                single_paid_mode = list(modes.values())[0]
            else:
                # have professional ignore other modes for historical reasons
                single_paid_mode = modes.get(CourseMode.PROFESSIONAL)
        if single_paid_mode:
            return {
                "sku": single_paid_mode.sku,
                "name": single_paid_mode.name,
                "min_price": single_paid_mode.min_price,
                "description": single_paid_mode.description,
            }
        return {}

    @property
    def ecommerce_checkout_link(self):
        """
        Returns the ecommerce checkout link for the course.
        """
        if self.single_paid_mode and self.single_paid_mode.sku:
            return self.ecomm_service.get_checkout_page_url(
                self.single_paid_mode.sku, course_run_keys=[self.course_key]
            )
        return None

    @property
    def course_image_urls(self):
        """
        Returns a list of course image URLs.
        """
        return self.course_overview.image_urls

    @property
    def start_date_is_still_default(self):
        """
        Returns a boolean indicating whether the course start date is still the default value.
        """
        return self.course_overview.start_date_is_still_default

    @property
    def advertised_start(self):
        """
        Returns the advertised start date of the course.
        """
        return self.course_overview.advertised_start

    @property
    def course_price(self):
        """
        Returns the course price, formatted with the currency symbol.
        """
        _, course_price = get_course_prices(self.course)
        return course_price

    @property
    def pre_requisite_courses(self):
        """
        Returns a list of pre-requisite courses for the course.
        """
        return get_prerequisite_courses_display(self.course)

    @property
    def about_sidebar_html(self):
        """
        Returns the HTML content for the course about section.
        """
        if ENABLE_COURSE_ABOUT_SIDEBAR_HTML.is_enabled():
            return clean_dangerous_html(
                get_course_about_section(self.request, self.course, "about_sidebar_html")
            )
        return None

    @property
    def overview(self):
        """
        Returns the overview HTML content for the course.
        """
        return clean_dangerous_html(
            get_course_about_section(self.request, self.course, "overview")
        )


@method_decorator(transaction.non_atomic_requests, name='dispatch')
class CoursewareInformation(RetrieveAPIView):
    """
    **Use Cases**

        Request details for a course

    **Example Requests**

        GET /api/courseware/course/{course_key}

    **Response Values**

        Body consists of the following fields:

        * access_expiration: An object detailing when access to this course will expire
            * expiration_date: (str) When the access expires, in ISO 8601 notation
            * masquerading_expired_course: (bool) Whether this course is expired for the masqueraded user
            * upgrade_deadline: (str) Last chance to upgrade, in ISO 8601 notation (or None if can't upgrade anymore)
            * upgrade_url: (str) Upgrade linke (or None if can't upgrade anymore)
        * celebrations: An object detailing which celebrations to render
            * first_section: (bool) If the first section celebration should render
                Note: Also uses information from frontend so this value is not final
            * streak_length_to_celebrate: (int) The streak length to celebrate for the learner
            * streak_discount_enabled: (bool) If the frontend should render an upgrade discount for hitting the streak
            * weekly_goal: (bool) If the weekly goal celebration should render
        * course_goals:
            * selected_goal:
                * days_per_week: (int) The number of days the learner wants to learn per week
                * subscribed_to_reminders: (bool) Whether the learner wants email reminders about their goal
            * weekly_learning_goal_enabled: Flag indicating if this feature is enabled for this call
        * effort: A textual description of the weekly hours of effort expected
            in the course.
        * end: Date the course ends, in ISO 8601 notation
        * enrollment: Enrollment status of authenticated user
            * mode: `audit`, `verified`, etc
            * is_active: boolean
        * enrollment_end: Date enrollment ends, in ISO 8601 notation
        * enrollment_start: Date enrollment begins, in ISO 8601 notation
        * entrance_exam_data: An object containing information about the course's entrance exam
            * entrance_exam_current_score: (float) The users current score on the entrance exam
            * entrance_exam_enabled: (bool) If the course has an entrance exam
            * entrance_exam_id: (str) The block id for the entrance exam if enabled. Will be a section (chapter)
            * entrance_exam_minimum_score_pct: (float) The minimum score a user must receive on the entrance exam
                to unlock the remainder of the course. Returned as a float (i.e. 0.7 for 70%)
            * entrance_exam_passed: (bool) Indicates if the entrance exam has been passed
        * id: A unique identifier of the course; a serialized representation
            of the opaque key identifying the course.
        * language: The language code for the course
        * media: An object that contains named media items.  Included here:
            * course_image: An image to show for the course.  Represented
              as an object with the following fields:
                * uri: The location of the image
        * name: Name of the course
        * offer: An object detailing upgrade discount information
            * code: (str) Checkout code
            * expiration_date: (str) Expiration of offer, in ISO 8601 notation
            * original_price: (str) Full upgrade price without checkout code; includes currency symbol
            * discounted_price: (str) Upgrade price with checkout code; includes currency symbol
            * percentage: (int) Amount of discount
            * upgrade_url: (str) Checkout URL
        * org: Name of the organization that owns the course
        * related_programs: A list of objects that contains program data related to the given course including:
            * progress: An object containing program progress:
                * complete: (int) Number of complete courses in the program (a course is completed if the user has
                    earned a certificate for any of the nested course runs)
                * in_progress: (int) Number of courses in the program that are in progress (a course is in progress if
                    the user has enrolled in any of the nested course runs)
                * not_started: (int) Number of courses in the program that have not been started
            * slug: (str) The program type
            * title: (str) The title of the program
            * url: (str) The link to the program's landing page
            * uuid: (str) A unique identifier of the program
        * short_description: A textual description of the course
        * start: Date the course begins, in ISO 8601 notation
        * start_display: Readably formatted start of the course
        * start_type: Hint describing how `start_display` is set. One of:
            * `"string"`: manually set by the course author
            * `"timestamp"`: generated from the `start` timestamp
            * `"empty"`: no start date is specified
        * pacing: Course pacing. Possible values: instructor, self
        * user_timezone: User's chosen timezone setting (or null for browser default)
        * user_has_passing_grade: Whether or not the effective user's grade is equal to or above the courses minimum
            passing grade
        * course_exit_page_is_active: Flag for the learning mfe on whether or not the course exit page should display
        * certificate_data: data regarding the effective user's certificate for the given course
        * verify_identity_url: URL for a learner to verify their identity. Only returned for learners enrolled in a
            verified mode. Will update to reverify URL if necessary.
        * verification_status: The verification status of the effective user in the course. Possible values:
            * 'none': No verification has been created for the user
            * 'expired': The verification has expired
            * 'approved': The verification has been approved
            * 'pending': The verification is pending
            * 'must_reverify': The user must reverify their identity
        * linkedin_add_to_profile_url: URL to add the effective user's certificate to a LinkedIn Profile.
        * user_needs_integrity_signature: Whether the user needs to sign the integrity agreement for the course
        * learning_assistant_enabled: Whether the Xpert Learning Assistant is enabled for the requesting user
        * show_courseware_link: Whether the courseware link should be shown in the course details page
        * is_course_full: Whether the course is full
        * can_enroll: Whether the user can enroll in the course
        * invitation_only: Whether the course is invitation only
        * is_shib_course: Whether the course is a Shibboleth course
        * allow_anonymous: Whether the course allows anonymous access
        * ecommerce_checkout: Whether the course has an ecommerce checkout
        * single_paid_mode: An object representing the single paid mode for the course, if it exists
            * sku: (str) The SKU for the single paid mode
            * name: (str) The name of the single paid mode
            * min_price: (str) The minimum price for the single paid mode, formatted with the currency symbol
            * description: (str) The description of the single paid mode
        * ecommerce_checkout_link: The ecommerce checkout link for the course, if it exists
        * course_image_urls: A list of course image URLs
        * start_date_is_still_default: Whether the course start date is still the default value
        * advertised_start: The advertised start date of the course
        * course_price: The course price, formatted with the currency symbol
        * pre_requisite_courses: A list of pre-requisite courses for the course
        * about_sidebar_html: The HTML content for the course about section, if enabled
        * display_number_with_default: The course number with the org name, if set
        * display_org_with_default: The org name with the course number, if set
        * content_type_gating_enabled: Whether the content type gating is enabled for the course
        * show_calculator: Whether the calculator should be shown in the course details page
        * can_access_proctored_exams: Whether the user is eligible to access proctored exams
        * notes: An object containing note settings for the course
            * enabled: Boolean indicating whether edxnotes feature is enabled for the course
            * visible: Boolean indicating whether notes are visible in the course
        * marketing_url: The marketing URL for the course
        * overview: The overview HTML content for the course
        * license: The license for the course

    **Parameters:**

        requested_fields (optional) comma separated list:
            If set, then only those fields will be returned.
        username (optional) username to masquerade as (if requesting user is staff)

    **Returns**

        * 200 on success with above fields.
        * 400 if an invalid parameter was sent or the username was not provided
          for an authenticated request.
        * 403 if a user who does not have permission to masquerade as
          another user specifies a username other than their own.
        * 404 if the course is not available or cannot be seen.
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )

    serializer_class = CourseInfoSerializer

    def set_last_seen_courseware_timezone(self, user):
        """
        The timezone in the user's account is frequently not set.
        This method sets a user's recent timezone that can be used as a fallback
        """
        if not user.id:
            return

        cache_key = 'browser_timezone_{}'.format(str(user.id))
        browser_timezone = self.request.query_params.get('browser_timezone', None)
        cached_value = TieredCache.get_cached_response(cache_key)
        if not cached_value.is_found:
            if browser_timezone:
                TieredCache.set_all_tiers(cache_key, str(browser_timezone), 86400)  # Refresh the cache daily
                LastSeenCoursewareTimezone.objects.update_or_create(
                    user=user,
                    defaults={'last_seen_courseware_timezone': browser_timezone},
                )

    def get_object(self):
        """
        Return the requested course object, if the user has appropriate
        permissions.
        """
        original_user = self.request.user
        if self.request.user.is_staff:
            username = self.request.GET.get('username', '') or self.request.user.username
        else:
            username = self.request.user.username
        course_key = CourseKey.from_string(self.kwargs['course_key_string'])
        overview = CoursewareMeta(
            course_key,
            self.request,
            username=username,
        )
        # Record course goals user activity for learning mfe courseware on web
        UserActivity.record_user_activity(self.request.user, course_key)

        # Record a user's browser timezone
        self.set_last_seen_courseware_timezone(original_user)

        return overview

    def get_serializer_context(self):
        """
        Return extra context to be used by the serializer class.
        """
        context = super().get_serializer_context()
        context['requested_fields'] = self.request.GET.get('requested_fields', None)
        return context

    def finalize_response(self, request, response, *args, **kwargs):
        """
        Return the final response, exposing the 'Date' header for computing relative time to the dates in the data.

        Important dates such as 'access_expiration' are enforced server-side based on correct time; client-side clocks
        are frequently substantially far off which could lead to inaccurate messaging and incorrect expectations.
        Therefore, any messaging about those dates should be based on the server time and preferably in relative terms
        (time remaining); the 'Date' header is a straightforward and generalizable way for client-side code to get this
        reference.
        """
        response = super().finalize_response(request, response, *args, **kwargs)
        # Adding this header should be moved to global middleware, not just this endpoint
        return expose_header('Date', response)


class SequenceMetadata(DeveloperErrorViewMixin, APIView):
    """
    **Use Cases**

        Request details for a sequence/subsection

    **Example Requests**

        GET /api/courseware/sequence/{usage_key}

    **Response Values**

        Body consists of the following fields:
            TODO

    **Returns**

        * 200 on success with above fields.
        * 400 if an invalid parameter was sent.
        * 403 if a user who does not have permission to masquerade as
          another user specifies a username other than their own.
        * 404 if the course/usage_key is not available or cannot be seen.
        * 422 if the usage key is valid but does not have sequence metadata (like a unit or a problem)
    """

    authentication_classes = (
        JwtAuthentication,
        SessionAuthenticationAllowInactiveUser,
    )

    def get(self, request, usage_key_string, *args, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
        """
        Return response to a GET request.
        """
        try:
            usage_key = UsageKey.from_string(usage_key_string)
        except InvalidKeyError as exc:
            raise NotFound(f"Invalid usage key: '{usage_key_string}'.") from exc

        staff_access = has_access(request.user, 'staff', usage_key.course_key)
        is_preview = request.GET.get('preview', '0') == '1'
        _, request.user = setup_masquerade(
            request,
            usage_key.course_key,
            staff_access=staff_access,
            reset_masquerade_data=True,
        )

        branch_type = (
            ModuleStoreEnum.Branch.draft_preferred
        ) if is_preview and staff_access else (
            ModuleStoreEnum.Branch.published_only
        )
        _modulestore = modulestore()
        with _modulestore.branch_setting(branch_type, usage_key.course_key):
            with _modulestore.bulk_operations(usage_key.course_key):
                sequence, _ = get_block_by_usage_id(
                    self.request,
                    str(usage_key.course_key),
                    str(usage_key),
                    disable_staff_debug_info=True,
                    will_recheck_access=True)

                if not hasattr(sequence, 'get_metadata'):
                    # Looks like we were asked for metadata on something that is not a sequence (or section).
                    return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY)

                view = STUDENT_VIEW
                if request.user.is_anonymous:
                    view = PUBLIC_VIEW

                context = {
                    'specific_masquerade': is_masquerading_as_specific_student(request.user, usage_key.course_key)
                }
                return Response(sequence.get_metadata(view=view, context=context))


class Resume(DeveloperErrorViewMixin, APIView):
    """
    **Use Cases**

        Request the last completed block in a course

    **Example Requests**

        GET /api/courseware/resume/{course_key}

    **Response Values**

        Body consists of the following fields:

            * block: the last completed block key
            * section: the key to the section
            * unit: the key to the unit
        If no completion data is available, the keys will be null

    **Returns**

        * 200 on success with above fields.
        * 400 if an invalid parameter was sent.
        * 403 if a user who does not have permission to masquerade as
          another user specifies a username other than their own.
        * 404 if the course is not available or cannot be seen.
    """

    authentication_classes = (
        JwtAuthentication,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated,)

    def get(self, request, course_key_string, *args, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
        """
        Return response to a GET request.
        """
        course_id = CourseKey.from_string(course_key_string)
        resp = {
            'block_id': None,
            'section_id': None,
            'unit_id': None,
        }

        try:
            block_key = get_key_to_last_completed_block(request.user, course_id)
            path = path_to_location(modulestore(), block_key, request, full_path=True)
            resp['section_id'] = str(path[2])
            resp['unit_id'] = str(path[3])
            resp['block_id'] = str(block_key)

        except (ItemNotFoundError, NoPathToItem, UnavailableCompletionData):
            pass  # leaving all the IDs as None indicates a redirect to the first unit in the course, as a backup

        return Response(resp)


class Celebration(DeveloperErrorViewMixin, APIView):
    """
    **Use Cases**

        Marks a particular celebration as complete

    **Example Requests**

        POST /api/courseware/celebration/{course_key}

    **Request Parameters**

        Body consists of the following fields:

            * first_section (bool): whether we should celebrate when a user finishes their first section of a course
            * weekly_goal (bool): whether we should celebrate when a user hits their weekly learning goal in a course

    **Returns**

        * 200 or 201 or 202 on success with above fields.
        * 400 if an invalid parameter was sent.
        * 404 if the course is not available or cannot be seen.
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated,)
    http_method_names = ['post']

    def post(self, request, course_key_string, *args, **kwargs):  # lint-amnesty, pylint: disable=unused-argument
        """
        Handle a POST request.
        """
        course_key = CourseKey.from_string(course_key_string)

        # Check if we're masquerading as someone else. If so, we should just ignore this request.
        _, user = setup_masquerade(
            request,
            course_key,
            staff_access=has_access(request.user, 'staff', course_key),
            reset_masquerade_data=True,
        )
        if user != request.user:
            return Response(status=202)  # "Accepted"

        data = dict(request.data)
        first_section = data.pop('first_section', None)
        weekly_goal = data.pop('weekly_goal', None)
        if data:
            return Response(status=400)  # there were parameters we didn't recognize

        enrollment = CourseEnrollment.get_enrollment(request.user, course_key)
        if not enrollment:
            return Response(status=404)

        defaults = {}
        if first_section is not None:
            defaults['celebrate_first_section'] = first_section
        if weekly_goal is not None:
            defaults['celebrate_weekly_goal'] = weekly_goal

        if defaults:
            _, created = CourseEnrollmentCelebration.objects.update_or_create(enrollment=enrollment, defaults=defaults)
            return Response(status=201 if created else 200)
        else:
            return Response(status=200)  # just silently allow it
