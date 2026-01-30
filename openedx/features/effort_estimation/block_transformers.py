"""
Effort Estimation Transformer implementation.
Adds effort estimations for block types it recognizes.
"""

import math

import crum
import lxml.html
from django.utils.functional import cached_property
from edxval.api import get_videos_for_course

from openedx.core.djangoapps.content.block_structure.transformer import BlockStructureTransformer
from openedx.core.lib.mobile_utils import is_request_from_mobile_app

from .toggles import EFFORT_ESTIMATION_DISABLED_FLAG


class EffortEstimationTransformer(BlockStructureTransformer):
    """
    A transformer that adds effort estimation to the block tree.

    There are two fields added by this transformer:
    - effort_activities: The number of "activities" at this block or lower. Note that verticals count as a single
                         activity at most. Activities are basically anything that isn't text or video.
    - effort_time: Our best guess at how long the block and lower will take, in seconds. We use an estimated reading
                   speed and video duration to calculate this. Just a rough guide.

    If there is any missing data (like no video duration), we don't provide any estimates at all for the course.
    We'd rather provide no estimate than a misleading estimate.

    This transformer requires data gathered during the collection phase (from a course publish), so it won't work
    on a course until the next publish.
    """
    WRITE_VERSION = 1
    READ_VERSION = 1

    # Public xblock field names
    EFFORT_ACTIVITIES = 'effort_activities'
    EFFORT_TIME = 'effort_time'

    # Private transformer field names
    DISABLE_ESTIMATION = 'disable_estimation'
    HTML_WORD_COUNT = 'html_word_count'
    VIDEO_CLIP_DURATION = 'video_clip_duration'
    VIDEO_DURATION = 'video_duration'

    CACHE_VIDEO_DURATIONS = 'video.durations'
    DEFAULT_WPM = 265  # words per minute

    class MissingEstimationData(Exception):
        pass

    @classmethod
    def name(cls):
        """
        Unique identifier for the transformer's class.
        This must match the entry point name in the package configuration.
        """
        return 'effort_estimation'

    @classmethod
    def collect(cls, block_structure):
        """
        Grabs raw estimates for leaf content.

        Pooling leaf estimates higher up the tree (e.g. in verticals, then sequentials, then chapters) is done by
        transform() below at run time, because which blocks each user sees can be different.
        """
        block_structure.request_xblock_fields('category')
        block_structure.request_xblock_fields('global_speed', 'only_on_web')  # video fields

        collection_cache = {}  # collection methods can stuff some temporary data here

        collections = {
            'html': cls._collect_html_effort,
            'video': cls._collect_video_effort,
        }

        try:
            for block_key in block_structure.topological_traversal():
                xblock = block_structure.get_xblock(block_key)

                if xblock.category in collections:
                    collections[xblock.category](block_structure, block_key, xblock, collection_cache)

        except cls.MissingEstimationData:
            # Some bit of required data is missing. Likely some duration info is missing from the video pipeline.
            # Rather than attempt to work around it, just set a note for ourselves to not show durations for this
            # course at all. Better no estimate than a misleading estimate.
            block_structure.set_transformer_data(cls, cls.DISABLE_ESTIMATION, True)

    @classmethod
    def _collect_html_effort(cls, block_structure, block_key, xblock, _cache):
        """Records a word count for later reading speed calculations."""
        try:
            text = lxml.html.fromstring(xblock.data).text_content() if xblock.data else ''
        except Exception as exc:  # pylint: disable=broad-except
            raise cls.MissingEstimationData() from exc

        block_structure.set_transformer_block_field(block_key, cls, cls.HTML_WORD_COUNT, len(text.split()))

    @classmethod
    def _collect_video_effort(cls, block_structure, block_key, xblock, cache):
        """Records a duration for later viewing speed calculations."""
        # Lookup all course video metadata at once rather than piecemeal, for performance reasons
        if cls.CACHE_VIDEO_DURATIONS not in cache:
            all_videos, _ = get_videos_for_course(str(block_structure.root_block_usage_key.course_key))
            cache[cls.CACHE_VIDEO_DURATIONS] = {v['edx_video_id']: v['duration'] for v in all_videos}

        # Check if we have a duration. If not, raise an exception that will stop this transformer from affecting
        # this course.
        duration = cache[cls.CACHE_VIDEO_DURATIONS].get(xblock.edx_video_id, 0)
        if duration <= 0:
            raise cls.MissingEstimationData()

        block_structure.set_transformer_block_field(block_key, cls, cls.VIDEO_DURATION, duration)

        # Some videos will suggest specific start & end times, rather than the whole video. Note that this is only
        # supported in some clients (other clients - like the mobile app - will play the whole video anyway). So we
        # record this duration separately, to use instead of the whole video duration if the client supports it.
        clip_duration = (xblock.end_time - xblock.start_time).total_seconds()
        if clip_duration > 0:
            block_structure.set_transformer_block_field(block_key, cls, cls.VIDEO_CLIP_DURATION, clip_duration)

    def transform(self, usage_info, block_structure):
        # Early exit if our per-course opt-out flag is enabled
        if EFFORT_ESTIMATION_DISABLED_FLAG.is_enabled(block_structure.root_block_usage_key.course_key):
            return

        # Skip any transformation if our collection phase said to
        cls = EffortEstimationTransformer
        if block_structure.get_transformer_data(cls, cls.DISABLE_ESTIMATION, default=False):
            return

        # These estimation methods should return a tuple of (a number in seconds, an activity count)
        estimations = {
            'chapter': self._estimate_children_effort,
            'course': self._estimate_children_effort,
            'html': self._estimate_html_effort,
            'sequential': self._estimate_children_effort,
            'vertical': self._estimate_vertical_effort,
            'video': self._estimate_video_effort,
        }

        # We're good to continue and make user-specific estimates based on collected data
        for block_key in block_structure.post_order_traversal():
            category = block_structure.get_xblock_field(block_key, 'category')
            if category not in estimations:
                continue

            time, activities = estimations[category](usage_info, block_structure, block_key)

            if time is not None:
                # We take the ceiling of the estimate here just for cleanliness. Losing the fractional seconds does
                # technically make our estimate less accurate, especially as we combine these values in parents.
                # But easier to present a simple integer to any consumers, and precise to-the-second accuracy on our
                # estimate is not a primary goal.
                block_structure.override_xblock_field(block_key, self.EFFORT_TIME, math.ceil(time))

            if activities is not None:
                block_structure.override_xblock_field(block_key, self.EFFORT_ACTIVITIES, activities)

    @cached_property
    def _is_on_mobile(self):
        """Returns whether the current request is from our mobile app."""
        request = crum.get_current_request()
        return request and is_request_from_mobile_app(request)

    def _gather_child_values(self, block_structure, block_key, field, default=0):
        """Collects and sums all child values for field."""
        return sum(
            block_structure.get_xblock_field(child_key, field, default=default)
            for child_key in block_structure.get_children(block_key)
        )

    def _estimate_children_effort(self, _usage_info, block_structure, block_key):
        """Collects time and activity counts for children."""
        time = self._gather_child_values(block_structure, block_key, self.EFFORT_TIME)
        time = time or None  # avoid claiming anything takes 0 seconds by coercing to None (no estimate) instead

        # Use 1 as the default for activity - any block that we don't know for sure is 0, we should count
        activities = self._gather_child_values(block_structure, block_key, self.EFFORT_ACTIVITIES, default=1)

        return time, activities

    def _estimate_html_effort(self, _usage_info, block_structure, block_key):
        """Returns an average expected time to read the contained html."""
        cls = EffortEstimationTransformer
        word_count = block_structure.get_transformer_block_field(block_key, cls, self.HTML_WORD_COUNT)
        if not word_count:
            return None, 0

        time = word_count / self.DEFAULT_WPM * 60  # in seconds
        return time, 0

    def _estimate_vertical_effort(self, usage_info, block_structure, block_key):
        """A vertical is either an amount of time if we know it, or an activity"""
        time, activities = self._estimate_children_effort(usage_info, block_structure, block_key)

        # Verticals are the basic activity metric - we may have collected all unknown xblocks as activities in the call
        # above, but we reset that count to 1 here.
        return time, 1 if activities else 0

    def _estimate_video_effort(self, _usage_info, block_structure, block_key):
        """Returns an expected time to view the video, at the user's preferred speed."""
        cls = EffortEstimationTransformer
        clip_duration = block_structure.get_transformer_block_field(block_key, cls, self.VIDEO_CLIP_DURATION)
        duration = block_structure.get_transformer_block_field(block_key, cls, self.VIDEO_DURATION)
        global_speed = block_structure.get_xblock_field(block_key, 'global_speed', default=1)
        only_on_web = block_structure.get_xblock_field(block_key, 'only_on_web', default=False)

        if self._is_on_mobile:
            if only_on_web:
                return None, 0
            clip_duration = None  # mobile can't do clips

        user_duration = clip_duration or duration
        if not user_duration:
            return None, 0

        # We are intentionally only looking at global_speed, not speed (which is last speed user used on this video)
        # because this estimate is meant to be somewhat static.
        return user_duration / global_speed, 0
