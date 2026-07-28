# pylint: disable=missing-module-docstring
import logging

import dateutil
import edx_api_doc_tools as apidocs
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from openedx_authz.constants.permissions import COURSES_VIEW_COURSE
from pytz import UTC
from rest_framework import serializers, status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from user_tasks.models import UserTaskStatus
from user_tasks.views import StatusViewSet

from cms.djangoapps.contentstore.course_info_model import get_course_updates
from cms.djangoapps.contentstore.tasks import migrate_course_legacy_library_blocks_to_item_bank
from cms.djangoapps.contentstore.views.certificates import CertificateManager
from common.djangoapps.util.proctoring import requires_escalation_email
from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.decorators import authz_permission_required
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from openedx.core.lib.api.serializers import StatusSerializerWithUuid
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, view_auth_classes
from xmodule.course_metadata_utils import DEFAULT_GRADING_POLICY  # pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

from .utils import course_author_access_required, get_bool_param, get_ready_to_migrate_legacy_library_content_blocks

log = logging.getLogger(__name__)


@view_auth_classes()
class CourseValidationView(DeveloperErrorViewMixin, GenericAPIView):
    """
    **Use Case**

    **Example Requests**

        GET /api/courses/v1/validation/{course_id}/

    **GET Parameters**

        A GET request may include the following parameters.

        * all
        * dates
        * assignments
        * grades
        * certificates
        * updates
        * proctoring
        * graded_only (boolean) - whether to included graded subsections only in the assignments information.
        * validate_oras (boolean) - whether to check the dates in ORA problems in addition to assignment due dates.

    **GET Response Values**

        The HTTP 200 response has the following values.

        * is_self_paced - whether the course is self-paced.
        * dates
            * has_start_date - whether the start date is set on the course.
            * has_end_date - whether the end date is set on the course.
        * assignments
            * total_number - total number of assignments in the course.
            * total_visible - number of assignments visible to learners in the course.
            * assignments_with_dates_before_start - assignments with due dates before the start date.
            * assignments_with_dates_after_end - assignments with due dates after the end date.
        * grades
            * sum_of_weights - sum of weights for all assignments in the course (valid ones should equal 1).
        * certificates
            * is_activated - whether the certificate is activated for the course.
            * has_certificate - whether the course has a certificate.
        * updates
            * has_update - whether at least one course update exists.
        * proctoring
            * needs_proctoring_escalation_email - whether the course requires a proctoring escalation email
            * has_proctoring_escalation_email - whether the course has a proctoring escalation email

    """
    # TODO: ARCH-91
    # This view is excluded from Swagger doc generation because it
    # does not specify a serializer class.
    swagger_schema = None

    @authz_permission_required(COURSES_VIEW_COURSE.identifier, LegacyAuthoringPermission.READ)
    def get(self, request, course_key):
        """
        Returns validation information for the given course.
        """
        all_requested = get_bool_param(request, 'all', False)

        store = modulestore()
        with store.bulk_operations(course_key):
            course = store.get_course(course_key, depth=self._required_course_depth(request, all_requested))

            response = dict(
                is_self_paced=course.self_paced,
            )
            if get_bool_param(request, 'dates', all_requested):
                response.update(
                    dates=self._dates_validation(course)
                )
            if get_bool_param(request, 'assignments', all_requested):
                response.update(
                    assignments=self._assignments_validation(course, request)
                )
            if get_bool_param(request, 'grades', all_requested):
                response.update(
                    grades=self._grades_validation(course)
                )
            if get_bool_param(request, 'certificates', all_requested):
                response.update(
                    certificates=self._certificates_validation(course)
                )
            if get_bool_param(request, 'updates', all_requested):
                response.update(
                    updates=self._updates_validation(course, request)
                )
            if get_bool_param(request, 'proctoring', all_requested):
                response.update(
                    proctoring=self._proctoring_validation(course)
                )

        return Response(response)

    def _required_course_depth(self, request, all_requested):
        if get_bool_param(request, 'assignments', all_requested):
            return 2
        else:
            return 0

    def _dates_validation(self, course):
        return dict(
            has_start_date=self._has_start_date(course),
            has_end_date=course.end is not None,
        )

    def _assignments_validation(self, course, request):  # pylint: disable=missing-function-docstring
        assignments, visible_assignments = self._get_assignments(course)
        assignments_with_dates = [
            a for a in visible_assignments if a.due
        ]
        assignments_with_dates_before_start = (
            [
                {'id': str(a.location), 'display_name': a.display_name}
                for a in assignments_with_dates
                if a.due < course.start
            ]
            if self._has_start_date(course)
            else []
        )

        assignments_with_dates_after_end = (
            [
                {'id': str(a.location), 'display_name': a.display_name}
                for a in assignments_with_dates
                if a.due > course.end
            ]
            if course.end
            else []
        )

        if get_bool_param(request, 'graded_only', False):
            assignments_with_dates = [
                a
                for a in visible_assignments
                if a.due and a.graded
            ]
            assignments_with_dates_before_start = (
                [
                    {'id': str(a.location), 'display_name': a.display_name}
                    for a in assignments_with_dates
                    if a.due < course.start
                ]
                if self._has_start_date(course)
                else []
            )

            assignments_with_dates_after_end = (
                [
                    {'id': str(a.location), 'display_name': a.display_name}
                    for a in assignments_with_dates
                    if a.due > course.end
                ]
                if course.end
                else []
            )

        assignments_with_ora_dates_before_start = []
        assignments_with_ora_dates_after_end = []
        if get_bool_param(request, 'validate_oras', False):
            # Iterate over all ORAs to find any with dates outside
            # acceptable range
            for ora in self._get_open_responses(
                course,
                get_bool_param(request, 'graded_only', False)
            ):
                if course.start and self._has_date_before_start(ora, course.start):
                    parent_unit = modulestore().get_item(ora.parent)
                    parent_assignment = modulestore().get_item(parent_unit.parent)
                    assignments_with_ora_dates_before_start.append({
                        'id': str(parent_assignment.location),
                        'display_name': parent_assignment.display_name
                    })
                if course.end and self._has_date_after_end(ora, course.end):
                    parent_unit = modulestore().get_item(ora.parent)
                    parent_assignment = modulestore().get_item(parent_unit.parent)
                    assignments_with_ora_dates_after_end.append({
                        'id': str(parent_assignment.location),
                        'display_name': parent_assignment.display_name
                    })

        return dict(
            total_number=len(assignments),
            total_visible=len(visible_assignments),
            assignments_with_dates_before_start=assignments_with_dates_before_start,
            assignments_with_dates_after_end=assignments_with_dates_after_end,
            assignments_with_ora_dates_before_start=assignments_with_ora_dates_before_start,
            assignments_with_ora_dates_after_end=assignments_with_ora_dates_after_end,
        )

    def _grades_validation(self, course):
        has_grading_policy = self._has_grading_policy(course)
        sum_of_weights = course.grader.sum_of_weights
        return dict(
            has_grading_policy=has_grading_policy,
            sum_of_weights=sum_of_weights,
        )

    def _certificates_validation(self, course):
        is_activated, certificates = CertificateManager.is_activated(course)
        certificates_enabled = CertificateManager.is_enabled(course)
        return dict(
            is_activated=is_activated,
            has_certificate=certificates_enabled and len(certificates) > 0,
            is_enabled=certificates_enabled,
        )

    def _updates_validation(self, course, request):
        updates_usage_key = course.id.make_usage_key('course_info', 'updates')
        updates = get_course_updates(updates_usage_key, provided_id=None, user_id=request.user.id)
        return dict(
            has_update=len(updates) > 0,
        )

    def _get_assignments(self, course):  # pylint: disable=missing-function-docstring
        store = modulestore()
        sections = [store.get_item(section_usage_key) for section_usage_key in course.children]
        assignments = [
            store.get_item(assignment_usage_key)
            for section in sections
            for assignment_usage_key in section.children
        ]
        visible_sections = [
            s for s in sections
            if not s.visible_to_staff_only and not s.hide_from_toc
        ]
        assignments_in_visible_sections = [
            store.get_item(assignment_usage_key)
            for visible_section in visible_sections
            for assignment_usage_key in visible_section.children
        ]
        visible_assignments = [
            a for a in assignments_in_visible_sections
            if not a.visible_to_staff_only
        ]
        return assignments, visible_assignments

    def _get_open_responses(self, course, graded_only):
        oras = modulestore().get_items(course.id, qualifiers={'category': 'openassessment'})
        return oras if not graded_only else [ora for ora in oras if ora.graded]

    def _has_date_before_start(self, ora, start):  # pylint: disable=missing-function-docstring
        if ora.submission_start:
            if dateutil.parser.parse(ora.submission_start).replace(tzinfo=UTC) < start:
                return True
        if ora.submission_due:
            if dateutil.parser.parse(ora.submission_due).replace(tzinfo=UTC) < start:
                return True
        for assessment in ora.rubric_assessments:
            if assessment['start']:
                if dateutil.parser.parse(assessment['start']).replace(tzinfo=UTC) < start:
                    return True
            if assessment['due']:
                if dateutil.parser.parse(assessment['due']).replace(tzinfo=UTC) < start:
                    return True

        return False

    def _has_date_after_end(self, ora, end):  # pylint: disable=missing-function-docstring
        if ora.submission_start:
            if dateutil.parser.parse(ora.submission_start).replace(tzinfo=UTC) > end:
                return True
        if ora.submission_due:
            if dateutil.parser.parse(ora.submission_due).replace(tzinfo=UTC) > end:
                return True
        for assessment in ora.rubric_assessments:
            if assessment['start']:
                if dateutil.parser.parse(assessment['start']).replace(tzinfo=UTC) > end:
                    return True
            if assessment['due']:
                if dateutil.parser.parse(assessment['due']).replace(tzinfo=UTC) > end:
                    return True
        return False

    def _has_start_date(self, course):
        return not course.start_date_is_still_default

    def _has_grading_policy(self, course):  # pylint: disable=missing-function-docstring
        grading_policy_formatted = {}
        default_grading_policy_formatted = {}

        for grader, assignment_type, weight in course.grader.subgraders:
            grading_policy_formatted[assignment_type] = {
                'type': assignment_type,
                'short_label': grader.short_label,
                'min_count': grader.min_count,
                'drop_count': grader.drop_count,
                'weight': weight,
            }

        # the default grading policy Lab assignment type does not have a short-label,
        # but courses with the default grading policy do return a short-label for Lab
        # assignments, so we ignore the Lab short-label
        if 'Lab' in grading_policy_formatted:
            grading_policy_formatted['Lab'].pop('short_label')

        for assignment in DEFAULT_GRADING_POLICY['GRADER']:
            default_assignment_grading_policy_formatted = {
                'type': assignment['type'],
                'min_count': assignment['min_count'],
                'drop_count': assignment['drop_count'],
                'weight': assignment['weight'],
            }

            # the default grading policy Lab assignment type does not have a short-label, so only
            # add short_label to dictionary when the assignment has one
            if 'short_label' in assignment:
                default_assignment_grading_policy_formatted['short_label'] = assignment['short_label']

            default_grading_policy_formatted[assignment['type']] = default_assignment_grading_policy_formatted

        # check for equality
        if len(grading_policy_formatted) != len(default_grading_policy_formatted):
            return True
        else:
            for assignment_type in grading_policy_formatted:
                if (assignment_type not in default_grading_policy_formatted or
                        grading_policy_formatted[assignment_type] != default_grading_policy_formatted[assignment_type]):
                    return True

        return False

    def _proctoring_validation(self, course):
        # A proctoring escalation email is required if 'required_escalation_email' is set on the proctoring backend
        return dict(
            needs_proctoring_escalation_email=requires_escalation_email(course.proctoring_provider),
            has_proctoring_escalation_email=bool(course.proctoring_escalation_email)
        )


class CourseLegacyLibraryContentSerializer(serializers.Serializer):
    usage_key = serializers.CharField()


class CourseLegacyLibraryContentMigratorView(DeveloperErrorViewMixin, StatusViewSet):
    """
    This endpoint is used for migrating legacy library content to the new item bank block library v2.
    """
    # DELETE is not allowed, as we want to preserve all task status objects.
    # Instead, users can POST to /cancel to cancel running tasks.
    http_method_names = ["get", "post"]
    authentication_classes = (
        BearerAuthenticationAllowInactiveUser,
        JwtAuthentication,
        SessionAuthenticationAllowInactiveUser,
    )
    serializer_class = StatusSerializerWithUuid

    @apidocs.schema(
        responses={
            200: CourseLegacyLibraryContentSerializer(many=True),
            401: "The requester is not authenticated.",
        },
    )
    @authz_permission_required(COURSES_VIEW_COURSE.identifier, LegacyAuthoringPermission.WRITE)
    def list(self, _, course_key):  # pylint: disable=arguments-differ
        """
        Returns all legacy library content blocks ready to be migrated to new item bank block.
        """
        blocks = get_ready_to_migrate_legacy_library_content_blocks(course_key)
        serializer = CourseLegacyLibraryContentSerializer(blocks, many=True)
        return Response(serializer.data)

    @apidocs.schema(
        responses={
            200: "In case of success, a 200.",
            401: "The requester is not authenticated.",
        },
    )
    @course_author_access_required
    def create(self, request, course_key):
        """
        Migrate all legacy library content blocks to new item bank block.
        """
        task = migrate_course_legacy_library_blocks_to_item_bank.delay(request.user.id, str(course_key))
        task_status = UserTaskStatus.objects.get(task_id=task.id)
        serializer = self.get_serializer(task_status)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
