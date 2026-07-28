"""
Progress Tab Views
"""

from django.contrib.auth import get_user_model
from django.http.response import Http404
from edx_django_utils import monitoring as monitoring_utils
from edx_django_utils.plugins import pluggable_override
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from opaque_keys.edx.keys import CourseKey
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from xblock.scorable import ShowCorrectness

from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.ccx.custom_exception import CCXLocatorValidationException
from lms.djangoapps.course_blocks.api import get_course_blocks
from lms.djangoapps.course_blocks.transformers import start_date
from lms.djangoapps.course_home_api.progress.api import aggregate_assignment_type_grade_summary
from lms.djangoapps.course_home_api.progress.serializers import ProgressTabSerializer
from lms.djangoapps.course_home_api.toggles import course_home_mfe_progress_tab_is_active
from lms.djangoapps.course_home_api.utils import get_course_or_403
from lms.djangoapps.courseware.access import has_access, has_ccx_coach_role
from lms.djangoapps.courseware.courses import get_course_blocks_completion_summary, get_studio_url
from lms.djangoapps.courseware.masquerade import setup_masquerade
from lms.djangoapps.courseware.views.views import credit_course_requirements, get_cert_data
from lms.djangoapps.grades.api import CourseGradeFactory
from lms.djangoapps.verify_student.services import IDVerificationService
from openedx.core.djangoapps.content.block_structure.api import get_block_structure_manager
from openedx.core.djangoapps.content.block_structure.transformers import BlockStructureTransformers
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from openedx.features.content_type_gating.block_transformers import ContentTypeGateTransformer
from openedx.features.course_duration_limits.access import get_access_expiration_data
from xmodule.modulestore.django import modulestore

User = get_user_model()


@pluggable_override('OVERRIDE_COURSE_HOME_PROGRESS_USERNAME')
def obfuscated_username(request, student):
    """
    Return an obfuscated username for the student, or None (indicating student's
    real name should be used).

    This function can be overridden by an installed plugin via the
    OVERRIDE_COURSE_HOME_PROGRESS_USERNAME setting to return a generic name
    for learners who should not have their real username exposed.
    """
    return None


class ProgressTabView(RetrieveAPIView):
    """
    **Use Cases**

        Request details for the Progress Tab

    **Example Requests**

        GET api/course_home/v1/progress/{course_key}
        GET api/course_home/v1/progress/{course_key}/{student_id}/

    **Response Values**

        Body consists of the following fields:

        access_expiration: An object detailing when access to this course will expire
            expiration_date: (str) When the access expires, in ISO 8601 notation
            masquerading_expired_course: (bool) Whether this course is expired for the masqueraded user
            upgrade_deadline: (str) Last chance to upgrade, in ISO 8601 notation (or None if can't upgrade anymore)
            upgrade_url: (str) Upgrade link (or None if can't upgrade anymore)
        certificate_data: Object containing information about the user's certificate status
            cert_status: (str) the status of a user's certificate (full list of statuses are defined in
                         lms/djangoapps/certificates/models.py)
            cert_web_view_url: (str) the url to view the certificate
            download_url: (str) the url to download the certificate
        completion_summary: Object containing unit completion counts with the following fields:
            complete_count: (float) number of complete units
            incomplete_count: (float) number of incomplete units
            locked_count: (float) number of units where contains_gated_content is True
        course_grade: Object containing the following fields:
            is_passing: (bool) whether the user's grade is above the passing grade cutoff
            letter_grade: (str) the user's letter grade based on the set grade range.
                                If user is passing, value may be 'A', 'B', 'C', 'D', 'Pass', otherwise none
            percent: (float) the user's total graded percent in the course
        credit_course_requirements: Object containing credit course requirements with the following fields:
            eligibility_status: (str) Indicates if the user is eligible to receive credit. Value may be
                "eligible", "not_eligible", or "partial_eligible"
            requirements: List of requirements that must be fulfilled to be eligible to receive credit. See
                `get_credit_requirement_status` for details on the fields
        end: (date) end date of the course
        enrollment_mode: (str) a str representing the enrollment the user has ('audit', 'verified', ...)
        grading_policy:
            assignment_policies: List of serialized assignment grading policy objects, each has the following fields:
                num_droppable: (int) the number of lowest scored assignments that will not be counted towards the final
                               grade
                short_label: (str) the abbreviated name given to the assignment type
                type: (str) the assignment type
                weight: (float) the percent weight the given assigment type has on the overall grade
            grade_range: an object containing the grade range cutoffs. The exact keys in the object can vary, but they
                         range from just 'Pass', to a combination of 'A', 'B', 'C', and 'D'. If a letter grade is
                         present, 'Pass' is not included.
        has_scheduled_content: (bool) boolean on if the course has content scheduled with a release date in the future
        section_scores: List of serialized Chapters. Each Chapter has the following fields:
            display_name: (str) a str of what the name of the Chapter is for displaying on the site
            subsections: List of serialized Subsections, each has the following fields:
                assignment_type: (str) the format, if any, of the Subsection (Homework, Exam, etc)
                block_key: (str) the key of the given subsection block
                display_name: (str) a str of what the name of the Subsection is for displaying on the site
                due: (str or None) the due date of the subsection in ISO 8601 format, or None if no due date is set
                has_graded_assignment: (bool) whether or not the Subsection is a graded assignment
                learner_has_access: (bool) whether the learner has access to the subsection (could be FBE gated)
                num_points_earned: (int) the amount of points the user has earned for the given subsection
                num_points_possible: (int) the total amount of points possible for the given subsection
                override: Optional object if grade has been overridden by proctor or grading change
                    system: (str) either GRADING or PROCTORING
                    reason: (str) a comment on the grading override
                percent_graded: (float) the percentage of total points the user has received a grade for in a given
                                subsection
                problem_scores: List of objects that represent individual problem scores with the following fields:
                    earned: (float) number of earned points
                    possible: (float) number of possible points
                show_correctness: (str) a str representing whether to show the problem/practice scores based on due date
                                  ('always', 'never', 'past_due', values defined in
                                   xmodule/modulestore/inheritance.py)
                show_grades: (bool) a bool for whether to show grades based on the access the user has
                url: (str or None) the absolute path url to the Subsection or None if the Subsection is no longer
                     accessible to the learner due to a hide_after_due course team setting
        studio_url: (str) a str of the link to the grading in studio for the course
        user_has_passing_grade: (bool) boolean on if the user has a passing grade in the course
        username: (str) username of the student whose progress information is being displayed.
        verification_data: an object containing
            link: (str) the link to either start or retry ID verification
            status: (str) the status of the ID verification
            status_date: (str) the date time string of when the ID verification status was set

    **Returns**

        * 200 on success with above fields.
        * 401 if the user is not authenticated or not enrolled.
        * 403 if the user does not have access to the course.
        * 404 if the course is not available or cannot be seen.
    """

    authentication_classes = (
        JwtAuthentication,
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (IsAuthenticated,)
    serializer_class = ProgressTabSerializer

    def _get_student_user(self, request, course_key, student_id, is_staff):
        """Gets the student User object, either from coaching, masquerading, or normal actual request"""
        if student_id:
            try:
                student_id = int(student_id)
            except ValueError as e:
                raise Http404 from e

        if student_id is None or student_id == request.user.id:
            _, student = setup_masquerade(
                request,
                course_key,
                staff_access=is_staff,
                reset_masquerade_data=True
            )
            return student

        # When a student_id is passed in, we display the progress page for the user
        # with the provided user id, rather than the requesting user
        try:
            coach_access = has_ccx_coach_role(request.user, course_key)
        except CCXLocatorValidationException:
            coach_access = False

        has_access_on_students_profiles = is_staff or coach_access
        # Requesting access to a different student's profile
        if not has_access_on_students_profiles:
            raise Http404

        try:
            return User.objects.get(id=student_id)
        except User.DoesNotExist as exc:
            raise Http404 from exc

    def _visible_section_scores(self, course_grade):
        """Return only those chapter/section scores that are visible to the learner."""
        visible_chapters = []
        for chapter in course_grade.chapter_grades.values():
            filtered_sections = [
                subsection
                for subsection in chapter["sections"]
                if getattr(subsection, "show_correctness", None) != ShowCorrectness.NEVER_BUT_INCLUDE_GRADE
            ]
            visible_chapters.append({**chapter, "sections": filtered_sections})
        return visible_chapters

    def get(self, request, *args, **kwargs):    # pylint: disable=too-many-statements
        course_key_string = kwargs.get('course_key_string')
        course_key = CourseKey.from_string(course_key_string)
        student_id = kwargs.get('student_id')

        if not course_home_mfe_progress_tab_is_active(course_key):
            raise Http404

        # Enable NR tracing for this view based on course
        monitoring_utils.set_custom_attribute('course_id', course_key_string)
        monitoring_utils.set_custom_attribute('user_id', request.user.id)
        monitoring_utils.set_custom_attribute('is_staff', request.user.is_staff)
        requester_has_staff_access = bool(has_access(request.user, 'staff', course_key))

        student = self._get_student_user(request, course_key, student_id, requester_has_staff_access)
        learner_has_staff_access = bool(has_access(student, 'staff', course_key))
        username = obfuscated_username(request, student) or student.username

        course = get_course_or_403(student, 'load', course_key, check_if_enrolled=False)

        course_overview = CourseOverview.get_from_id(course_key)
        enrollment = CourseEnrollment.get_enrollment(student, course_key)
        enrollment_mode = getattr(enrollment, 'mode', None)

        if not (enrollment and enrollment.is_active) and not requester_has_staff_access:
            return Response('User not enrolled.', status=401)

        # The block structure is used for both the course_grade and has_scheduled content fields
        # So it is called upfront and reused for optimization purposes
        collected_block_structure = get_block_structure_manager(course_key).get_collected()
        course_grade = CourseGradeFactory().read(student, collected_block_structure=collected_block_structure)

        # recalculate course grade from visible grades (stored grade was calculated over all grades, visible or not)
        course_grade.update(visible_grades_only=True, has_staff_access=learner_has_staff_access)

        # Get has_scheduled_content data
        transformers = BlockStructureTransformers()
        transformers += [start_date.StartDateTransformer(), ContentTypeGateTransformer()]
        usage_key = collected_block_structure.root_block_usage_key
        course_blocks = get_course_blocks(
            student,
            usage_key,
            transformers=transformers,
            collected_block_structure=collected_block_structure,
            include_has_scheduled_content=True
        )
        has_scheduled_content = course_blocks.get_xblock_field(usage_key, 'has_scheduled_content')

        # Get user_has_passing_grade data
        user_has_passing_grade = False
        if not student.is_anonymous:
            user_grade = course_grade.percent
            user_has_passing_grade = user_grade >= course.lowest_passing_grade

        block = modulestore().get_course(course_key)
        grading_policy = block.grading_policy
        disable_progress_graph = block.disable_progress_graph
        verification_status = IDVerificationService.user_status(student)
        verification_link = None
        if verification_status['status'] is None or verification_status['status'] == 'expired':
            verification_link = IDVerificationService.get_verify_location(course_id=course_key)
        elif verification_status['status'] == 'must_reverify':
            verification_link = IDVerificationService.get_verify_location(course_id=course_key)
        verification_data = {
            'link': verification_link,
            'status': verification_status['status'],
            'status_date': verification_status['status_date'],
        }

        access_expiration = get_access_expiration_data(request.user, course_overview)

        # Aggregations delegated to helper functions for reuse and testability
        assignment_type_grade_summary = aggregate_assignment_type_grade_summary(
            course_grade,
            grading_policy,
            has_staff_access=learner_has_staff_access,
        )

        # Filter out section scores to only have those that are visible to the user
        section_scores = self._visible_section_scores(course_grade)

        data = {
            'access_expiration': access_expiration,
            'certificate_data': get_cert_data(student, course, enrollment_mode, course_grade),
            'completion_summary': get_course_blocks_completion_summary(course_key, student),
            'course_grade': course_grade,
            'credit_course_requirements': credit_course_requirements(course_key, student),
            'end': course.end,
            'enrollment_mode': enrollment_mode,
            'grading_policy': grading_policy,
            'has_scheduled_content': has_scheduled_content,
            'section_scores': section_scores,
            'studio_url': get_studio_url(course, 'settings/grading'),
            'username': username,
            'user_has_passing_grade': user_has_passing_grade,
            'verification_data': verification_data,
            'disable_progress_graph': disable_progress_graph,
            'assignment_type_grade_summary': assignment_type_grade_summary["results"],
            'final_grades': assignment_type_grade_summary["final_grades"],
        }
        context = self.get_serializer_context()
        context['staff_access'] = learner_has_staff_access
        context['course_blocks'] = course_blocks
        context['course_key'] = course_key
        # course_overview and enrollment will be used by VerifiedModeSerializer
        context['course_overview'] = course_overview
        context['enrollment'] = enrollment
        serializer = self.get_serializer_class()(data, context=context)

        return Response(serializer.data)
