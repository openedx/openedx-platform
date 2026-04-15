"""
Xblock services for creating xblocks.
"""

from uuid import uuid4

from django.utils.translation import gettext as _

from cms.djangoapps.models.settings.course_grading import CourseGradingModel
from openedx.core.toggles import ENTRANCE_EXAMS
from xmodule.modulestore.django import modulestore
from xmodule.tabs import StaticTab

from ..helpers import GRADER_TYPES, remove_entrance_exam_graders
from .xblock_helpers import usage_key_with_run


def create_xblock(parent_locator, user, category, display_name, boilerplate=None, is_entrance_exam=False):
    """
    Performs the actual grunt work of creating items/xblocks -- knows nothing about requests, views, etc.
    """
    store = modulestore()
    usage_key = usage_key_with_run(parent_locator)
    with store.bulk_operations(usage_key.course_key):
        parent = store.get_item(usage_key)
        dest_usage_key = usage_key.replace(category=category, name=uuid4().hex)

        # get the metadata, display_name, and definition from the caller
        metadata = {}
        data = None
        template_id = boilerplate
        if template_id:
            base_clz = parent.runtime.load_block_type(category)
            dynamic_clz = parent.runtime.mixologist.mix(base_clz)
            if dynamic_clz is not None:
                template = dynamic_clz.get_template(template_id)
                if template is not None:
                    metadata = template.get('metadata', {})
                    data = template.get('data')

        if display_name is not None:
            metadata['display_name'] = display_name

        # We should use the 'fields' kwarg for newer block settings/values (vs. metadata or data)
        fields = {}

        # Entrance Exams: Chapter module positioning
        child_position = None
        if ENTRANCE_EXAMS.is_enabled():
            if category == 'chapter' and is_entrance_exam:
                fields['is_entrance_exam'] = is_entrance_exam
                fields['in_entrance_exam'] = True  # Inherited metadata, all children will have it
                child_position = 0

        # TODO need to fix components that are sending definition_data as strings, instead of as dicts
        # For now, migrate them into dicts here.
        if isinstance(data, str):
            data = {'data': data}

        created_block = store.create_child(
            user.id,
            usage_key,
            dest_usage_key.block_type,
            block_id=dest_usage_key.block_id,
            fields=fields,
            definition_data=data,
            metadata=metadata,
            runtime=parent.runtime,
            position=child_position,
        )

        # Entrance Exams: Grader assignment
        if ENTRANCE_EXAMS.is_enabled():
            course_key = usage_key.course_key
            course = store.get_course(course_key)
            if hasattr(course, 'entrance_exam_enabled') and course.entrance_exam_enabled:
                if category == 'sequential' and parent_locator == course.entrance_exam_id:
                    # Clean up any pre-existing entrance exam graders
                    remove_entrance_exam_graders(course_key, user)
                    grader = {
                        "type": GRADER_TYPES['ENTRANCE_EXAM'],
                        "min_count": 0,
                        "drop_count": 0,
                        "short_label": "Entrance",
                        "weight": 0
                    }
                    grading_model = CourseGradingModel.update_grader_from_json(
                        course.id,
                        grader,
                        user
                    )
                    CourseGradingModel.update_section_grader_type(
                        created_block,
                        grading_model['type'],
                        user
                    )

        # VS[compat] cdodge: This is a hack because static_tabs also have references from the course block, so
        # if we add one then we need to also add it to the policy information (i.e. metadata)
        # we should remove this once we can break this reference from the course to static tabs
        if category == 'static_tab':
            display_name = display_name or _("Empty")  # Prevent name being None
            course = store.get_course(dest_usage_key.course_key)
            course.tabs.append(
                StaticTab(
                    name=display_name,
                    url_slug=dest_usage_key.block_id,
                )
            )
            store.update_item(course, user.id)

        return created_block
