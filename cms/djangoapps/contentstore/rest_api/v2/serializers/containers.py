"""Serializers for a course container."""

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

CONTAINER_MINIMAL_VIEW = "minimal"
CONTAINER_FULL_VIEW = "full"


class CourseContainerAncestorSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A block above the container, below the course."""

    id = serializers.CharField(help_text="Usage key of the block.")
    category = serializers.CharField(
        help_text="Block type, such as chapter (section), sequential (subsection), vertical (unit) or split_test.",
    )
    display_name = serializers.CharField(help_text="Name of the block shown to authors.")

    class Meta:
        ref_name = "CourseContainerAncestor"


class CourseContainerSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A section, subsection, unit or other container of a course, and where it sits in the course."""

    id = serializers.CharField(help_text="Usage key of the container.")
    category = serializers.CharField(
        help_text=(
            "Block type of the container: chapter for a section, sequential for a subsection, vertical for a "
            "unit, or another container type such as split_test."
        ),
    )
    display_name = serializers.CharField(help_text="Name of the container shown to authors.")
    display_type = serializers.CharField(
        allow_null=True,
        help_text=(
            "Name of the container's type shown to authors, such as Section, Subsection, Unit, or Vertical "
            "inside a unit. Null for a block type that declares no display name."
        ),
    )
    course_key = serializers.CharField(help_text="Key of the course the container belongs to.")
    is_unit = serializers.BooleanField(
        help_text="Whether the container is a unit: a vertical placed directly in a subsection.",
    )
    unit_id = serializers.CharField(
        allow_null=True,
        help_text=(
            "Usage key of the unit: the container itself, or the unit it is placed in. Null for a section, a "
            "subsection, or a container placed directly in one, which no unit holds."
        ),
    )
    position = serializers.IntegerField(
        min_value=1,
        allow_null=True,
        help_text=(
            "Position of that unit among the children of its subsection, starting at 1. Children that cannot "
            "be loaded are not counted. Null when unit_id is null."
        ),
    )
    ancestors = CourseContainerAncestorSerializer(
        many=True,
        help_text=(
            "The blocks above the container, from its section down to its direct parent, leaving out the "
            "course: empty for a section, the section for a subsection, the section and subsection for a unit, "
            "and also the unit and any container between for a container inside a unit."
        ),
    )

    class Meta:
        ref_name = "CourseContainer"


class CourseContainerXBlockSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The container's name and type."""

    display_name = serializers.CharField(help_text="Name of the container shown to authors.")
    display_type = serializers.CharField(
        allow_null=True,
        help_text="Name of the container's type shown to authors. Null for a block type that declares no display name.",
    )
    category = serializers.CharField(help_text="Block type of the container.")

    class Meta:
        ref_name = "CourseContainerXBlock"


class CourseContainerAncestorChildSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A block listed beside one of the container's ancestors."""

    url = serializers.CharField(
        allow_null=True,
        help_text=(
            "Studio address of the block's page. Null for a component, such as a problem, which has no page "
            "of its own."
        ),
    )
    display_name = serializers.CharField(help_text="Name of the block shown to authors.")
    usage_key = serializers.CharField(help_text="Usage key of the block.")

    class Meta:
        ref_name = "CourseContainerAncestorChild"


class CourseContainerAncestorXBlockSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One ancestor of the container, with the blocks beside it."""

    children = CourseContainerAncestorChildSerializer(
        many=True,
        help_text=(
            "Every child of this ancestor's parent, in course order, this ancestor among them: every section "
            "of the course for the section, every subsection of the section for the subsection, and so on."
        ),
    )
    title = serializers.CharField(help_text="Name of this ancestor shown to authors.")
    is_last = serializers.BooleanField(help_text="Whether this ancestor is the container's direct parent.")

    class Meta:
        ref_name = "CourseContainerAncestorXBlock"


@extend_schema_field({"oneOf": [{"type": "boolean"}, {"type": "string"}]})
class _SupportLevelField(serializers.Field):
    """A support level, returned as stored: either true, or a level code."""

    def to_representation(self, value):
        return value


class CourseContainerComponentTemplateSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A kind of component that authors can add to the container."""

    display_name = serializers.CharField(help_text="Name of the template shown to authors.")
    category = serializers.CharField(help_text="Block type the template creates.")
    boilerplate_name = serializers.CharField(
        allow_null=True,
        help_text="Name of the boilerplate the new block is filled from. Null for a blank block.",
    )
    hinted = serializers.BooleanField(help_text="Whether the template is a problem with hints.")
    tab = serializers.CharField(help_text="Tab of the add-component menu the template is shown in: common or advanced.")
    support_level = _SupportLevelField(
        help_text=(
            "How well the template is supported: true when support levels are not configured, otherwise fs "
            "(fully supported), ps (provisionally supported) or us (unsupported)."
        ),
    )
    single_instance = serializers.BooleanField(
        required=False,
        help_text="Present and true when the container may hold only one block of this type.",
    )
    disabled = serializers.BooleanField(
        required=False,
        help_text="Present and true when authors may not add this template to the container.",
    )
    disabled_reason = serializers.CharField(
        required=False,
        help_text="Why the template may not be added. Present only with a reason.",
    )

    class Meta:
        ref_name = "CourseContainerComponentTemplate"


class CourseContainerSupportLegendSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Whether and how the add-component menu explains support levels."""

    show_legend = serializers.BooleanField(help_text="Whether the support level legend is shown.")
    allow_unsupported_xblocks = serializers.BooleanField(
        required=False,
        help_text="Whether the course allows unsupported components. Absent for the container's own types.",
    )
    documentation_label = serializers.CharField(
        required=False,
        help_text="Heading of the support level legend. Absent for the container's own types.",
    )

    class Meta:
        ref_name = "CourseContainerSupportLegend"


class CourseContainerComponentGroupSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A button of the add-component menu and the templates it offers."""

    type = serializers.CharField(help_text="Block type of the group, or advanced for the advanced components.")
    templates = CourseContainerComponentTemplateSerializer(many=True, help_text="Templates the group offers.")
    display_name = serializers.CharField(help_text="Name of the group shown to authors.")
    support_legend = CourseContainerSupportLegendSerializer(help_text="How support levels are explained.")
    beta = serializers.BooleanField(
        required=False,
        help_text="Whether the group's block type is in beta. Absent on the advanced group and on groups "
                  "the container defines for itself.",
    )

    class Meta:
        ref_name = "CourseContainerComponentGroup"


class CourseContainerFullSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Everything the Studio unit page shows for a unit, or for a container inside a unit."""

    language_code = serializers.CharField(help_text="Language the response is written in.")
    xblock = CourseContainerXBlockSerializer(help_text="The container's name and type.")
    is_unit_page = serializers.BooleanField(
        help_text="Whether the container is a unit: a vertical placed directly in a subsection.",
    )
    is_collapsible = serializers.BooleanField(
        help_text="Whether the container is a library content block, whose children authors can collapse.",
    )
    position = serializers.IntegerField(
        min_value=1,
        help_text=(
            "Position of the container's unit among the children of its subsection, starting at 1. Children "
            "that cannot be loaded are not counted."
        ),
    )
    prev_url = serializers.CharField(
        allow_null=True,
        help_text=(
            "URL-encoded Studio address of the unit before the container's unit, crossing into the previous "
            "subsection or section when needed. Null when there is no previous unit to go to: for the first "
            "unit of the course, and when the neighbouring subsection or section it would cross into holds no "
            "units."
        ),
    )
    next_url = serializers.CharField(
        allow_null=True,
        help_text=(
            "URL-encoded Studio address of the unit after the container's unit, crossing into the next "
            "subsection or section when needed. Null when there is no next unit to go to: for the last unit "
            "of the course, and when the neighbouring subsection or section it would cross into holds no "
            "units."
        ),
    )
    new_unit_category = serializers.CharField(help_text="Block type of a new unit: always vertical.")
    outline_url = serializers.CharField(help_text="Studio address of the course outline.")
    ancestor_xblocks = CourseContainerAncestorXBlockSerializer(
        many=True,
        help_text=(
            "The container's ancestors, outermost first and down to its direct parent, leaving out the topmost "
            "one, the course. Each comes with the blocks beside it."
        ),
    )
    component_templates = CourseContainerComponentGroupSerializer(
        many=True,
        help_text=(
            "The add-component menu: one group per component type the course enables, then the advanced "
            "group when it has templates. A container that declares the block types it holds gets one group "
            "per declared type instead."
        ),
    )
    xblock_info = serializers.DictField(
        help_text=(
            "The container as the Studio unit page reads it. Always carries actions, ancestor_has_staff_lock, "
            "category, course_graders, display_name, due, due_date, edited_on, edited_on_raw, embed_lms_url, "
            "enable_hide_from_toc_ui, explanatory_message, format, graded, group_access, has_changes, "
            "has_children, has_explicit_staff_lock, hide_from_toc, id, is_tagging_feature_disabled, lms_url, "
            "published, published_on, relative_weeks_due, release_date, released_to_students, "
            "show_correctness, start, studio_url, upstream_info, user_partition_info, user_partitions, "
            "visibility_state and xblock_type. "
            "A unit also carries ancestor_info, currently_visible_to_students, discussion_enabled, edited_by, "
            "enable_copy_paste_units, has_partition_group_components, published_by and staff_lock_from, plus "
            "release_date_from when release_date is set. ancestor_info.ancestors lists the unit's subsection, "
            "section and course, each as an object of the same kind; the subsection's carries child_info, "
            "whose children are the subsection's units. "
            "Some keys depend on configuration: course_authoring_url and taxonomy_tags_widget_url are absent "
            "when tagging is disabled, and a unit carries tags only while tagging is enabled; "
            "video_sharing_enabled, video_sharing_options and video_sharing_doc_url appear when public video "
            "sharing is on; a unit carries summary_configuration_enabled when content summaries are configured."
        ),
    )
    draft_preview_link = serializers.CharField(
        allow_null=True,
        help_text="Address of the container's draft preview in the LMS. Null when no LMS address is configured.",
    )
    published_preview_link = serializers.CharField(
        allow_null=True,
        help_text="Address of the container's published version in the LMS. Null when no LMS address is configured.",
    )
    show_unit_tags = serializers.BooleanField(help_text="Whether tagging is enabled, so unit tags are shown.")
    is_fullwidth_content = serializers.BooleanField(
        help_text="Whether the container is a library content block, which the page shows at full width.",
    )
    assets_url = serializers.CharField(help_text="Studio address of the course's files and uploads page.")
    unit_block_id = serializers.CharField(help_text="Block identifier of the container's unit, without its course.")
    subsection_location = serializers.CharField(help_text="Usage key of the subsection holding the container's unit.")
    course_sequence_ids = serializers.ListField(
        child=serializers.CharField(),
        help_text="Usage keys of every subsection of the course, in course order.",
    )
    library_content_picker_url = serializers.CharField(
        allow_null=True,
        help_text=(
            "Address of the page that picks content from a library into the course. Null when the new content "
            "libraries are not enabled."
        ),
    )

    class Meta:
        ref_name = "CourseContainerFull"


class CourseContainerQuerySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Query parameters of a container read. A parameter sent with an empty value counts as omitted."""

    view = serializers.ChoiceField(
        choices=[CONTAINER_MINIMAL_VIEW, CONTAINER_FULL_VIEW],
        default=CONTAINER_MINIMAL_VIEW,
        error_messages={"invalid_choice": "Must be minimal or full."},
        help_text=(
            "minimal (the default) returns the container and the blocks above it. full returns everything "
            "the Studio unit page shows for it, and is available only for a unit or a container inside a unit."
        ),
    )

    class Meta:
        ref_name = "CourseContainerQuery"
