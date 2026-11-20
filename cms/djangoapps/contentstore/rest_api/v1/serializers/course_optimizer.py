"""Serializers for the course optimizer's link checks and previous-run link updates."""

from collections.abc import Mapping

from rest_framework import serializers

from cms.djangoapps.contentstore.rest_api.v1.course_optimizer_service import ALL_LINKS, RUN_STATES, SINGLE_LINKS

MINIMAL_VIEW = "minimal"
FULL_VIEW = "full"


class QueuedRunSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The state of a run that has just been queued."""

    status = serializers.ChoiceField(
        choices=RUN_STATES,
        help_text="State of the run: always Pending, as the run waits to be picked up.",
    )

    class Meta:
        ref_name = "CourseOptimizerQueuedRun.v1"


class PreviousRunLinkSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A link to the previous run of the course, and whether it now points at this run."""

    original_link = serializers.CharField(source="originalLink", help_text="The link as it points at the previous run.")
    is_updated = serializers.BooleanField(
        source="isUpdated", help_text="Whether the link has been changed to point at this run.",
    )
    updated_link = serializers.CharField(
        source="updatedLink",
        allow_null=True,
        help_text="The link as it points at this run, or null when it has not been changed.",
    )

    class Meta:
        ref_name = "LinkCheckPreviousRunLink.v1"


class LinkCheckReportBlockSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A component, course update, handouts page or custom page with the links found in it, by kind."""

    id = serializers.CharField(help_text="Usage key of the component or page, or the id of the course update.")
    display_name = serializers.CharField(
        source="displayName", allow_blank=True, help_text="Name of the component or page as Studio shows it.",
    )
    url = serializers.CharField(help_text="Path of the Studio page where the content is edited.")
    broken_links = serializers.ListField(
        source="brokenLinks", child=serializers.CharField(), help_text="Links that could not be reached.",
    )
    locked_links = serializers.ListField(
        source="lockedLinks",
        child=serializers.CharField(),
        help_text="Links to Studio content that refused access, such as locked files.",
    )
    external_forbidden_links = serializers.ListField(
        source="externalForbiddenLinks",
        child=serializers.CharField(),
        help_text="Links to other sites that refused access.",
    )
    previous_run_links = PreviousRunLinkSerializer(
        source="previousRunLinks", many=True, help_text="Links that point at the previous run of the course.",
    )

    class Meta:
        ref_name = "LinkCheckReportBlock.v1"


class LinkCheckReportUnitSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A unit holding components with flagged links."""

    id = serializers.CharField(help_text="Usage key of the unit.")
    display_name = serializers.CharField(
        source="displayName", allow_blank=True, help_text="Name of the unit as Studio shows it.",
    )
    blocks = LinkCheckReportBlockSerializer(many=True, help_text="Components of the unit with flagged links.")

    class Meta:
        ref_name = "LinkCheckReportUnit.v1"


class LinkCheckReportSubsectionSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A subsection holding units with flagged links."""

    id = serializers.CharField(help_text="Usage key of the subsection.")
    display_name = serializers.CharField(
        source="displayName", allow_blank=True, help_text="Name of the subsection as Studio shows it.",
    )
    units = LinkCheckReportUnitSerializer(many=True, help_text="Units of the subsection with flagged links.")

    class Meta:
        ref_name = "LinkCheckReportSubsection.v1"


class LinkCheckReportSectionSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A section holding subsections with flagged links."""

    id = serializers.CharField(help_text="Usage key of the section.")
    display_name = serializers.CharField(
        source="displayName", allow_blank=True, help_text="Name of the section as Studio shows it.",
    )
    subsections = LinkCheckReportSubsectionSerializer(
        many=True, help_text="Subsections of the section with flagged links.",
    )

    class Meta:
        ref_name = "LinkCheckReportSection.v1"


class LinkCheckReportSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    The links a check flagged, where they were found.

    Course content is reported as sections, subsections, units and components,
    four levels and no more, in course order; only the parts holding flagged
    links appear. Course updates (with the handouts page last) and custom
    pages are listed whether or not they hold any.
    """

    sections = LinkCheckReportSectionSerializer(many=True, help_text="Sections with flagged links, in course order.")
    course_updates = LinkCheckReportBlockSerializer(
        many=True, help_text="Course updates and the handouts page, with the links found in each.",
    )
    custom_pages = LinkCheckReportBlockSerializer(
        many=True, help_text="Custom pages of the course, with the links found in each.",
    )

    class Meta:
        ref_name = "LinkCheckReport.v1"


class LinkCheckSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The latest link check of a course that the requester may see."""

    status = serializers.ChoiceField(
        choices=RUN_STATES,
        help_text=(
            "State of the run. Uninitiated when the requester may see no run of this course; "
            "Scanning and Updating are not reported for link checks."
        ),
    )
    created_at = serializers.DateTimeField(
        allow_null=True, help_text="When the run was queued, or null when there is no run.",
    )
    error = serializers.CharField(
        allow_null=True,
        help_text="A fixed message when the run failed or was canceled with an error recorded; otherwise null.",
    )
    output = LinkCheckReportSerializer(
        required=False,
        allow_null=True,
        help_text="Only with view=full: the links the run flagged, or null unless the run succeeded.",
    )

    class Meta:
        ref_name = "LinkCheck.v1"


class LinkToUpdateSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A link to change so that it points at this run instead of the previous one."""

    url = serializers.CharField(help_text="The link as it appears in the content.")
    type = serializers.CharField(
        help_text=(
            "Where the link was found, which decides how it is changed: 'course_content' for a component "
            "of the course outline, 'course_updates' for a course update (or the handouts page, when id names "
            "it), 'handouts' for the handouts page, and 'custom_pages' for a custom page. Other values are "
            "treated as 'course_content'."
        ),
    )
    id = serializers.CharField(
        help_text="Usage key of the component or page holding the link, or the course update id.",
    )

    class Meta:
        ref_name = "LinkToUpdate.v1"


class PreviousRunLinkUpdateRequestSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Which links to the previous run of the course to change."""

    action = serializers.ChoiceField(
        choices=[ALL_LINKS, SINGLE_LINKS],
        help_text="'all' changes every link to the previous run the course holds; 'single' changes the links in data.",
    )
    data = LinkToUpdateSerializer(
        many=True,
        required=False,
        help_text=(
            "The links to change, as a link check reports them. Required, and not empty, when action is "
            "'single'. Not read when action is 'all', so it is neither validated nor used then."
        ),
    )

    class Meta:
        ref_name = "PreviousRunLinkUpdateRequest.v1"

    def to_internal_value(self, data):
        """Drop ``data`` before validation when every link is to be changed, as it is not read then."""
        if isinstance(data, Mapping) and data.get("action") == ALL_LINKS:
            data = {key: value for key, value in data.items() if key != "data"}
        return super().to_internal_value(data)

    def validate(self, attrs):
        if attrs["action"] == SINGLE_LINKS and not attrs.get("data"):
            raise serializers.ValidationError({"data": ["This field is required when action is 'single'."]})
        return attrs


class LinkUpdateResultSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The outcome of changing one link."""

    id = serializers.CharField(
        allow_blank=True, help_text="Usage key of the component or page holding the link, or the course update id.",
    )
    type = serializers.CharField(allow_blank=True, help_text="Where the link was found.")
    original_url = serializers.CharField(
        allow_blank=True, allow_null=True, help_text="The link before the change, or null when it was not recorded.",
    )
    new_url = serializers.CharField(
        allow_blank=True, help_text="The link after the change; the original link when it could not be changed.",
    )
    success = serializers.BooleanField(help_text="Whether the link was changed.")
    error_message = serializers.CharField(
        allow_null=True, help_text="A fixed message when the link could not be changed; otherwise null.",
    )

    class Meta:
        ref_name = "LinkUpdateResult.v1"


class PreviousRunLinkUpdateSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The latest update of links to the previous run that the requester may see."""

    status = serializers.ChoiceField(
        choices=RUN_STATES,
        help_text=(
            "State of the run; Scanning and Updating are the stages of a run in progress. "
            "Uninitiated when the requester may see no run of this course."
        ),
    )
    results = LinkUpdateResultSerializer(
        many=True,
        required=False,
        help_text="Only with view=full: the outcome of each link of a run that succeeded; empty otherwise.",
    )

    class Meta:
        ref_name = "PreviousRunLinkUpdate.v1"


def _view_field(full_view_adds):
    return serializers.ChoiceField(
        choices=[MINIMAL_VIEW, FULL_VIEW],
        default=MINIMAL_VIEW,
        help_text=(
            f"Which representation to return. 'minimal' returns the state of the run; 'full' adds {full_view_adds}."
        ),
    )


class LinkCheckQuerySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Query parameters of a read of a course's latest link check."""

    view = _view_field("the links the run flagged, which can be many")

    class Meta:
        ref_name = "LinkCheckQuery.v1"


class PreviousRunLinkUpdateQuerySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Query parameters of a read of a course's latest update of links to its previous run."""

    view = _view_field("the outcome of each link the run changed")

    class Meta:
        ref_name = "PreviousRunLinkUpdateQuery.v1"
