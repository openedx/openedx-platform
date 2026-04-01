"""Utilities for retrieving Open Response Assessments (ORAs) data for instructor dashboards."""

from django.utils.translation import gettext as _
from django.conf import settings

from openassessment.data import OraAggregateData

from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order

DEFAULT_ORA_METRICS = {
    'total': 0,
    'training': 0,
    'peer': 0,
    'self': 0,
    'waiting': 0,
    'staff': 0,
    'done': 0,
}


def get_open_response_assessment_list(course):
    """
    Return a list of Open Response Assessments (ORAs) for a course.

    Uses OraAggregateData to collect response metrics, which transparently
    supports both ORA1 and ORA2 data.
    """
    course_key = course.id
    store = modulestore()

    # Collect ORA2 response metrics keyed by block location
    ora2_responses = OraAggregateData.collect_ora2_responses(str(course_key))

    # Fetch all openassessment blocks in the course
    openassessment_blocks = store.get_items(
        course_key,
        qualifiers={'category': 'openassessment'},
    )

    parents_cache = {}
    ora_items = []
    ora_grading_base_url = getattr(settings, 'ORA_GRADING_MICROFRONTEND_URL', None)

    for block in openassessment_blocks:
        block_id = str(block.location)
        parent_id = block.parent

        # Cache parent lookups to avoid repeated modulestore calls
        if parent_id not in parents_cache:
            parents_cache[parent_id] = store.get_item(parent_id)

        parent_block = parents_cache[parent_id]

        assessment_name = (
            _("Team") + " : " + block.display_name
            if block.teams_enabled
            else block.display_name
        )

        staff_ora_grading_url = None

        has_staff_assessment = 'staff-assessment' in block.assessment_steps
        is_team_enabled = block.teams_enabled
        if ora_grading_base_url and has_staff_assessment and not is_team_enabled:
            # Always generate a URL that points to the ORA Grading Microfrontend (MFE).
            #
            # During the migration to the ORA microfrontend,
            # only provide the grading URL for non-team assignments with staff assessment.
            # This logic was based on the original implementation in instructor_dashboard:
            #   - lms/djangoapps/instructor/views/instructor_dashboard.py
            #     (_section_open_response_assessment)
            #   - edx-ora2:
            #     https://github.com/openedx/edx-ora2/blob/801fbd14ebb059ab8c5ee8d5a39c260c7f87ab81/
            #     openassessment/xblock/static/js/src/lms/oa_course_items_listing.js#L73
            staff_ora_grading_url = f"{ora_grading_base_url}/{block_id}"

        ora_assessment_data = {
            'id': block_id,
            'name': assessment_name,
            'parent_name': parent_block.display_name,
            'staff_ora_grading_url': staff_ora_grading_url,
            **DEFAULT_ORA_METRICS,
        }

        # Merge collected metrics (if any)
        ora_assessment_data.update(ora2_responses.get(block_id, {}))
        ora_items.append(ora_assessment_data)

    return ora_items


def get_ora_summary(course):
    """
    Return aggregated ORA statistics for a course.
    """
    ora_items = get_open_response_assessment_list(course)
    summary = {
        'total_units': 0,
        'total_assessments': 0,
        'total_responses': 0,
        'training': 0,
        'peer': 0,
        'self': 0,
        'waiting': 0,
        'staff': 0,
        'final_grade_received': 0,
    }
    for item in ora_items:
        summary['total_assessments'] += 1
        summary['total_units'] += 1  # Assuming one assessment per unit
        summary['total_responses'] += item['total']
        summary['training'] += item['training']
        summary['peer'] += item['peer']
        summary['self'] += item['self']
        summary['waiting'] += item['waiting']
        summary['staff'] += item['staff']
        summary['final_grade_received'] += item['done']
    return summary
