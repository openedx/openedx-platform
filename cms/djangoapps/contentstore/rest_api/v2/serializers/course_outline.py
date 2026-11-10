"""Serializers for a course's outline."""

from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey
from opaque_keys.edx.locator import BlockUsageLocator
from rest_framework import serializers

OUTLINE_MINIMAL_VIEW = "minimal"
OUTLINE_FULL_VIEW = "full"
OUTLINE_DEFAULT_DEPTH = 2
OUTLINE_MAX_DEPTH = 3
NOT_AN_OUTLINE_BLOCK_MESSAGE = "No container in this course's outline has this key."


def _run_of(course_key):
    """The course run ``course_key`` names, whatever branch or version it pins."""
    return course_key.for_branch(None).version_agnostic()


class CourseOutlineNodeSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One block of the course outline, with its children down to the requested depth."""

    id = serializers.CharField(help_text="Usage key of the block.")
    category = serializers.CharField(
        help_text=(
            "Block type: course, chapter (section), sequential (subsection), vertical (unit), another "
            "container such as split_test, or a component such as problem placed directly in a section "
            "or subsection."
        ),
    )
    display_name = serializers.CharField(help_text="Name of the block shown to authors.")
    children_url = serializers.URLField(
        required=False,
        help_text=(
            "Address that returns this block with its children, one level deep. Present instead of "
            "children on a container the outline opens whose children lie below the requested depth."
        ),
    )

    def get_fields(self):
        fields = super().get_fields()
        fields["children"] = CourseOutlineNodeSerializer(
            many=True,
            required=False,
            help_text=(
                "Child blocks in course order. Present on a container the outline opens when its "
                "children lie within the requested depth, and empty when it has none. Units and "
                "components carry neither children nor children_url."
            ),
        )
        return fields

    class Meta:
        ref_name = "CourseOutline"


class CourseOutlineInitialStateSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The outline block to scroll to, and the blocks to show expanded with it."""

    locator_to_show = serializers.CharField(help_text="Usage key of the block requested with show.")
    expanded_locators = serializers.ListField(
        child=serializers.CharField(),
        help_text="Usage keys of that block and every block below it, in outline order.",
    )

    class Meta:
        ref_name = "CourseOutlineInitialState"


class CourseOutlineDeprecatedBlocksSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Blocks of deprecated types that the course still uses."""

    deprecated_enabled_block_types = serializers.ListField(
        child=serializers.CharField(),
        help_text="Deprecated block types enabled in the course's advanced settings.",
    )
    blocks = serializers.ListField(
        child=serializers.ListField(child=serializers.CharField(allow_null=True)),
        help_text=(
            "One pair per block of a deprecated type: the Studio address of the unit holding it, "
            "then the block's name, null when it has none."
        ),
    )
    advance_settings_url = serializers.CharField(help_text="Studio address of the course's advanced settings.")

    class Meta:
        ref_name = "CourseOutlineDeprecatedBlocks"


class CourseOutlineProctoringSettingSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The stored advanced setting a proctoring problem was found in."""

    deprecated = serializers.BooleanField(help_text="Whether the setting is deprecated.")
    display_name = serializers.CharField(help_text="Name of the setting shown to authors.")
    help = serializers.CharField(help_text="Help text shown to authors for the setting.")
    hide_on_enabled_publisher = serializers.BooleanField(
        help_text="Whether the setting is hidden when an external course publisher is enabled.",
    )
    value = serializers.CharField(help_text="The setting's stored value.")

    class Meta:
        ref_name = "CourseOutlineProctoringSetting"


class CourseOutlineProctoringErrorSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A problem found in the course's stored proctoring settings."""

    key = serializers.CharField(help_text="Name of the advanced setting at fault, such as proctoring_provider.")
    message = serializers.CharField(help_text="Description of the problem, for authors.")
    model = CourseOutlineProctoringSettingSerializer(help_text="The setting at fault as stored.")

    class Meta:
        ref_name = "CourseOutlineProctoringError"


class CourseOutlineFullSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The course outline with everything the Studio outline page shows beside it."""

    course_release_date = serializers.CharField(
        help_text='Course start date formatted for display, or "Set Date" when no start date is set.',
    )
    course_structure = serializers.DictField(
        help_text=(
            "The whole outline down to units, as the Studio outline page reads it: the course block, "
            "whose child_info.children holds its sections, and so on down to units and to components "
            "placed directly in a section or subsection. child_info is an object with category, "
            "display_name and children on the course, sections and subsections, with children only on "
            "other containers, and is absent on units and components. "
            "Every block carries actions, ancestor_has_staff_lock, category, course_authoring_url, "
            "course_graders, course_tags_count, display_name, due, due_date, edited_on, edited_on_raw, "
            "embed_lms_url, enable_copy_paste_units, enable_hide_from_toc_ui, explanatory_message, format, "
            "graded, group_access, has_changes, has_children, has_explicit_staff_lock, "
            "has_partition_group_components, hide_from_toc, hide_from_toc_message, id, "
            "is_tagging_feature_disabled, lms_url, published, published_on, relative_weeks_due, "
            "release_date, released_to_students, show_correctness, staff_only_message, start, studio_url, "
            "tag_counts_by_block, taxonomy_tags_widget_url, upstream_info, user_partition_info, "
            "user_partitions, visibility_state and xblock_type. "
            "The course also carries end, highlights_enabled_for_messaging and unit_level_discussions; the "
            "course and sections highlights_doc_url, highlights_enabled and highlights_preview_only; "
            "sections highlights; subsections hide_after_due; units discussion_enabled. "
            "Some keys depend on configuration: course_authoring_url, course_tags_count, "
            "tag_counts_by_block and taxonomy_tags_widget_url are absent when tagging is disabled; "
            "video_sharing_enabled, video_sharing_options and video_sharing_doc_url appear when public "
            "video sharing is on; with special exams enabled the course carries enable_proctored_exams, "
            "create_zendesk_tickets and enable_timed_exams, and subsections is_proctored_exam, "
            "was_exam_ever_linked_with_external, online_proctoring_rules, is_practice_exam, "
            "is_onboarding_exam, is_time_limited, exam_review_rules, default_time_limit_minutes, "
            "proctoring_exam_configuration_link, supports_onboarding and show_review_rules; with "
            "subsection gating on, subsections carry is_prereq, prereqs, prereq, prereq_min_score and "
            "prereq_min_completion; a subsection of an entrance exam carries is_header_visible; units "
            "carry summary_configuration_enabled when content summaries are configured."
        ),
    )
    deprecated_blocks_info = CourseOutlineDeprecatedBlocksSerializer(
        help_text="Blocks of deprecated types used in the course.",
    )
    discussions_incontext_feedback_url = serializers.CharField(
        help_text="Address where authors give feedback on in-context discussions. Empty when not configured.",
    )
    discussions_incontext_learnmore_url = serializers.CharField(
        help_text="Address of the in-context discussions documentation. Empty when not configured.",
    )
    discussions_settings = serializers.DictField(
        help_text=(
            "The course's discussion settings as stored, plus discussion_configuration_url, the address "
            "of the discussion configuration page. A course that was never configured carries "
            "enable_in_context, enable_graded_units and unit_level_visibility. A configured course may "
            "also carry provider_type, provider, posting_restrictions and an object of settings named "
            "after its provider."
        ),
    )
    initial_state = CourseOutlineInitialStateSerializer(
        allow_null=True,
        help_text="The block requested with show and the blocks to expand. Null without show or when no "
                  "outline block has that key.",
    )
    language_code = serializers.CharField(help_text="Language the response is written in.")
    lms_link = serializers.CharField(
        allow_null=True,
        help_text="Address of the course in the LMS. Null when no LMS address is configured.",
    )
    mfe_proctored_exam_settings_url = serializers.CharField(
        help_text="Address of the course's proctored exam settings. Empty when proctoring is not available.",
    )
    notification_dismiss_url = serializers.CharField(
        allow_null=True,
        help_text="Studio address that dismisses the rerun notification. Null when there is none.",
    )
    proctoring_errors = CourseOutlineProctoringErrorSerializer(
        many=True,
        help_text="Problems found in the course's stored proctoring settings.",
    )
    reindex_link = serializers.CharField(
        allow_null=True,
        help_text="Studio address that reindexes the course for search. Null when search indexing is off or "
                  "the requester may not reindex the course.",
    )
    rerun_notification_id = serializers.IntegerField(
        allow_null=True,
        help_text="Identifier of the course rerun status still shown to authors. Null when there is none.",
    )
    advance_settings_url = serializers.CharField(help_text="Studio address of the course's advanced settings.")
    is_custom_relative_dates_active = serializers.BooleanField(
        help_text="Whether custom relative due dates are enabled for the course.",
    )
    created_on = serializers.DateTimeField(
        allow_null=True,
        help_text="When the course was created. Null when the course has no overview record.",
    )

    class Meta:
        ref_name = "CourseOutlineFull"


class CourseOutlineQuerySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    Query parameters of a course outline read.

    A parameter sent with an empty value counts as omitted. Needs the course in
    the path as ``course_key`` in its context, to refuse a ``usage_key`` from
    another course.
    """

    view = serializers.ChoiceField(
        choices=[OUTLINE_MINIMAL_VIEW, OUTLINE_FULL_VIEW],
        required=False,
        help_text=(
            "minimal (the default) returns the outline tree to the requested depth. full returns the whole "
            "outline with everything the Studio outline page shows beside it."
        ),
    )
    depth = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=OUTLINE_MAX_DEPTH,
        help_text=(
            f"Levels of blocks below the root to include, from 1 to {OUTLINE_MAX_DEPTH}. Defaults to "
            f"{OUTLINE_DEFAULT_DEPTH}: sections and subsections. Minimal view only."
        ),
    )
    usage_key = serializers.CharField(
        required=False,
        help_text=(
            "Usage key of a container in this course's outline, such as a section, subsection or unit, "
            "to return in place of the whole course. Minimal view only."
        ),
    )
    show = serializers.CharField(
        required=False,
        help_text="Usage key of a block to report in initial_state with the blocks to expand. Full view only.",
    )

    def validate_usage_key(self, value):
        """
        Parse the key and accept only a key of a block in the course run in the path.

        Whether the block is in the course's outline is checked when the outline
        is read.
        """
        try:
            usage_key = UsageKey.from_string(value)
        except InvalidKeyError as error:
            raise serializers.ValidationError(NOT_AN_OUTLINE_BLOCK_MESSAGE) from error
        if (
            not isinstance(usage_key, BlockUsageLocator)
            or usage_key.deprecated
            # A key naming only a version has no course run to compare.
            or usage_key.course_key.org is None
            or _run_of(usage_key.course_key) != _run_of(self.context["course_key"])
        ):
            raise serializers.ValidationError(NOT_AN_OUTLINE_BLOCK_MESSAGE)
        return usage_key

    def validate(self, attrs):
        view = attrs.get("view") or OUTLINE_MINIMAL_VIEW
        if view == OUTLINE_FULL_VIEW:
            errors = {
                name: ["Applies to the minimal view only."]
                for name in ("depth", "usage_key") if name in attrs
            }
        else:
            errors = {"show": ["Applies to the full view only."]} if "show" in attrs else {}
        if errors:
            raise serializers.ValidationError(errors)
        attrs["view"] = view
        if view == OUTLINE_MINIMAL_VIEW:
            attrs.setdefault("depth", OUTLINE_DEFAULT_DEPTH)
        return attrs

    class Meta:
        ref_name = "CourseOutlineQuery"
