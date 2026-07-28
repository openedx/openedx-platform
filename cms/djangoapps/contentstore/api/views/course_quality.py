# pylint: disable=missing-module-docstring
import logging
import time

import numpy as np
from edxval.api import get_course_videos_qset
from openedx_authz.constants.permissions import COURSES_VIEW_COURSE
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from scipy import stats

from openedx.core.djangoapps.authz.constants import LegacyAuthoringPermission
from openedx.core.djangoapps.authz.decorators import authz_permission_required
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, view_auth_classes
from openedx.core.lib.cache_utils import request_cached
from openedx.core.lib.graph_traversals import traverse_pre_order
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

from .utils import get_bool_param

log = logging.getLogger(__name__)


@view_auth_classes()
class CourseQualityView(DeveloperErrorViewMixin, GenericAPIView):
    """
    **Use Case**

    **Example Requests**

        GET /api/courses/v1/quality/{course_id}/

    **GET Parameters**

        A GET request may include the following parameters.

        * all
        * sections
        * subsections
        * units
        * videos
        * exclude_graded (boolean) - whether to exclude graded subsections in the subsections and units information.

    **GET Response Values**

        The HTTP 200 response has the following values.

        * is_self_paced - whether the course is self-paced.
        * sections
            * total_number - number of sections in the course.
            * total_visible - number of sections visible to learners in the course.
            * number_with_highlights - number of sections that have at least one highlight entered.
            * highlights_enabled - whether highlights are enabled in the course.
        * subsections
            * total_visible - number of subsections visible to learners in the course.
            * num_with_one_block_type - number of visible subsections containing only one type of block.
            * num_block_types - statistics for number of block types across all visible subsections.
                * min
                * max
                * mean
                * median
                * mode
        * units
            * total_visible - number of units visible to learners in the course.
            * num_blocks - statistics for number of block across all visible units.
                * min
                * max
                * mean
                * median
                * mode
        * videos
            * total_number - number of video blocks in the course.
            * num_with_val_id - number of video blocks that include video pipeline IDs.
            * num_mobile_encoded - number of videos encoded through the video pipeline.
            * durations - statistics for video duration across all videos encoded through the video pipeline.
                * min
                * max
                * mean
                * median
                * mode

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
        def _execute_method_and_log_time(log_time, func, *args):
            """
            Call func passed in method with logging the time it took to complete.
            Logging is temporary, we will remove this once we get required information.
            """
            if log_time:
                start_time = time.time()
                output = func(*args)
                log.info('[%s] completed in [%f]', func.__name__, (time.time() - start_time))
            else:
                output = func(*args)
            return output

        all_requested = get_bool_param(request, 'all', False)

        store = modulestore()
        with store.bulk_operations(course_key):
            course = store.get_course(course_key, depth=self._required_course_depth(request, all_requested))
            # Added for EDUCATOR-3660
            course_key_harvard = str(course_key) == 'course-v1:HarvardX+SW12.1x+2016'

            response = dict(
                is_self_paced=course.self_paced,
            )
            if get_bool_param(request, 'sections', all_requested):
                response.update(
                    sections=_execute_method_and_log_time(course_key_harvard, self._sections_quality, course)
                )
            if get_bool_param(request, 'subsections', all_requested):
                response.update(
                    subsections=_execute_method_and_log_time(
                        course_key_harvard, self._subsections_quality, course, request
                    )
                )
            if get_bool_param(request, 'units', all_requested):
                response.update(
                    units=_execute_method_and_log_time(course_key_harvard, self._units_quality, course, request)
                )
            if get_bool_param(request, 'videos', all_requested):
                response.update(
                    videos=_execute_method_and_log_time(course_key_harvard, self._videos_quality, course)
                )

        return Response(response)

    def _required_course_depth(self, request, all_requested):  # pylint: disable=missing-function-docstring
        if get_bool_param(request, 'units', all_requested):
            # The num_blocks metric for "units" requires retrieving all blocks in the graph.
            return None
        elif get_bool_param(request, 'subsections', all_requested):
            # The num_block_types metric for "subsections" requires retrieving all blocks in the graph.
            return None
        elif get_bool_param(request, 'sections', all_requested):
            return 1
        else:
            return 0

    def _sections_quality(self, course):
        sections, visible_sections = self._get_sections(course)
        sections_with_highlights = [section for section in visible_sections if section.highlights]
        return dict(
            total_number=len(sections),
            total_visible=len(visible_sections),
            number_with_highlights=len(sections_with_highlights),
            highlights_active_for_course=course.highlights_enabled_for_messaging,
            highlights_enabled=True,  # used to be controlled by a waffle switch, now just always enabled
        )

    def _subsections_quality(self, course, request):  # pylint: disable=missing-function-docstring
        subsection_unit_dict = self._get_subsections_and_units(course, request)
        num_block_types_per_subsection_dict = {}
        for subsection_key, unit_dict in subsection_unit_dict.items():
            leaf_block_types_in_subsection = (
                unit_info['leaf_block_types']
                for unit_info in unit_dict.values()
            )
            num_block_types_per_subsection_dict[subsection_key] = len(set().union(*leaf_block_types_in_subsection))

        return dict(
            total_visible=len(num_block_types_per_subsection_dict),
            num_with_one_block_type=list(num_block_types_per_subsection_dict.values()).count(1),
            num_block_types=self._stats_dict(list(num_block_types_per_subsection_dict.values())),
        )

    def _units_quality(self, course, request):  # pylint: disable=missing-function-docstring
        subsection_unit_dict = self._get_subsections_and_units(course, request)
        num_leaf_blocks_per_unit = [
            unit_info['num_leaf_blocks']
            for unit_dict in subsection_unit_dict.values()
            for unit_info in unit_dict.values()
        ]
        return dict(
            total_visible=len(num_leaf_blocks_per_unit),
            num_blocks=self._stats_dict(num_leaf_blocks_per_unit),
        )

    def _videos_quality(self, course):  # pylint: disable=missing-function-docstring
        video_blocks_in_course = modulestore().get_items(course.id, qualifiers={'category': 'video'})
        video_durations = [cv.video.duration for cv in get_course_videos_qset(course.id)]

        return dict(
            total_number=len(video_blocks_in_course),
            num_mobile_encoded=len(video_durations),
            num_with_val_id=len([v for v in video_blocks_in_course if v.edx_video_id]),
            durations=self._stats_dict(video_durations),
        )

    @classmethod
    @request_cached()
    def _get_subsections_and_units(cls, course, request):
        """
        Returns {subsection_key: {unit_key: {num_leaf_blocks: <>, leaf_block_types: set(<>) }}}
        for all visible subsections and units.
        """
        _, visible_sections = cls._get_sections(course)
        subsection_dict = {}
        for section in visible_sections:
            visible_subsections = cls._get_visible_children(section)

            if get_bool_param(request, 'exclude_graded', False):
                visible_subsections = [s for s in visible_subsections if not s.graded]

            for subsection in visible_subsections:
                unit_dict = {}
                visible_units = cls._get_visible_children(subsection)

                for unit in visible_units:
                    leaf_blocks = cls._get_leaf_blocks(unit)
                    unit_dict[unit.location] = dict(
                        num_leaf_blocks=len(leaf_blocks),
                        leaf_block_types={block.location.block_type for block in leaf_blocks},
                    )

                subsection_dict[subsection.location] = unit_dict
        return subsection_dict

    @classmethod
    @request_cached()
    def _get_sections(cls, course):
        return cls._get_all_children(course)

    @classmethod
    def _get_all_children(cls, parent):  # pylint: disable=missing-function-docstring
        store = modulestore()
        children = [store.get_item(child_usage_key) for child_usage_key in cls._get_children(parent)]
        visible_children = [
            c for c in children
            if not c.visible_to_staff_only and not c.hide_from_toc
        ]
        return children, visible_children

    @classmethod
    def _get_visible_children(cls, parent):
        _, visible_chidren = cls._get_all_children(parent)
        return visible_chidren

    @classmethod
    def _get_children(cls, parent):  # pylint: disable=missing-function-docstring
        if not hasattr(parent, 'children'):
            return []
        else:
            return parent.children

    @classmethod
    def _get_leaf_blocks(cls, unit):  # pylint: disable=missing-function-docstring
        def leaf_filter(block):
            return (
                block.location.block_type not in ('chapter', 'sequential', 'vertical') and
                len(cls._get_children(block)) == 0
            )

        return list(traverse_pre_order(unit, cls._get_visible_children, leaf_filter))

    def _stats_dict(self, data):  # pylint: disable=missing-function-docstring
        if not data:
            return dict(
                min=None,
                max=None,
                mean=None,
                median=None,
                mode=None,
            )
        else:
            return dict(
                min=min(data),
                max=max(data),
                mean=np.around(np.mean(data)),
                median=np.around(np.median(data)),
                mode=stats.mode(data, axis=None)[0],
            )
