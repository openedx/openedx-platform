"""Serializers for a course's group configurations."""

from rest_framework import serializers

MINIMAL_VIEW = "minimal"
FULL_VIEW = "full"


class _FullViewUsage:
    """
    Leave out ``usage`` unless the serializer context asks for it.

    The context entry ``include_usage`` is read from the outermost serializer,
    so one flag decides for configurations and their groups alike.
    """

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self.context.get("include_usage"):
            data.pop("usage", None)
        return data


class GroupConfigurationValidationSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A summary of the problems Studio finds in a content experiment."""

    type = serializers.CharField(help_text="How serious the problems are: 'warning' or 'error'.")
    text = serializers.CharField(help_text="Message describing the problems, as Studio shows it.")

    class Meta:
        ref_name = "GroupConfigurationValidation.v2"


class GroupConfigurationUsageSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One place in the course content that uses a configuration or one of its groups."""

    label = serializers.CharField(
        help_text="Where the content is, as '<unit name> / <component name>'.",
    )
    url = serializers.CharField(help_text="Studio address of the page showing the content.")
    validation = GroupConfigurationValidationSerializer(
        required=False,
        allow_null=True,
        help_text=(
            "Present only for the content experiments using an experiment configuration: a summary of the "
            "experiment's problems, or null when it has none."
        ),
    )

    class Meta:
        ref_name = "GroupConfigurationUsage.v2"

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if "validation" not in instance:
            data.pop("validation")
        return data


class GroupConfigurationGroupSerializer(_FullViewUsage, serializers.Serializer):  # pylint: disable=abstract-method
    """One group of a group configuration."""

    id = serializers.IntegerField(help_text="Identifier of the group within its configuration.")
    name = serializers.CharField(help_text="Name of the group.")
    usage = GroupConfigurationUsageSerializer(
        many=True,
        required=False,
        allow_null=True,
        help_text=(
            "Present only with view=full. The content restricted to this group, or null for the groups of "
            "experiment configurations, whose use is reported for the whole configuration."
        ),
    )
    version = serializers.IntegerField(help_text="Version of the group's stored format.")

    class Meta:
        ref_name = "GroupConfigurationGroup.v2"


class GroupConfigurationSerializer(_FullViewUsage, serializers.Serializer):  # pylint: disable=abstract-method
    """
    One group configuration of a course, with what the caller may do with it.

    The scheme tells the kinds apart: 'random' for the configurations of
    content experiments, 'cohort' for content groups, and schemes such as
    'enrollment_track' or 'content_type_gate' for configurations the platform
    maintains, which are read only.
    """

    id = serializers.IntegerField(
        help_text=(
            "Identifier of the configuration within the course. A course without content groups lists an "
            "unsaved content group configuration whose identifier is chosen afresh for each request until "
            "a group is added to it."
        ),
    )
    name = serializers.CharField(help_text="Name of the configuration.")
    scheme = serializers.CharField(
        help_text=(
            "How learners are assigned to the groups, such as random, cohort, enrollment_track, "
            "content_type_gate or team."
        ),
    )
    description = serializers.CharField(allow_blank=True, help_text="Description of the configuration.")
    parameters = serializers.DictField(
        help_text="Settings of the scheme, such as the course_id of an enrollment track configuration.",
    )
    groups = GroupConfigurationGroupSerializer(many=True, help_text="The groups of the configuration.")
    usage = GroupConfigurationUsageSerializer(
        many=True,
        required=False,
        allow_null=True,
        help_text=(
            "Present only with view=full. The content experiments using an experiment configuration, or "
            "null for other configurations, whose use is reported per group."
        ),
    )
    version = serializers.IntegerField(help_text="Version of the configuration's stored format.")
    active = serializers.BooleanField(help_text="Whether the configuration is in use for learners.")
    read_only = serializers.BooleanField(
        required=False,
        help_text=(
            "Whether the configuration is maintained by the platform and cannot be changed by course authors. "
            "Absent for experiment configurations and for the unsaved content group configuration."
        ),
    )
    can_manage = serializers.BooleanField(
        help_text=(
            "Whether the caller may create, change and delete the course's group configurations. It is the "
            "same on every configuration of the course and does not override read_only: a read-only "
            "configuration, such as the enrollment track configuration, cannot be changed even when this is true."
        ),
    )

    class Meta:
        ref_name = "GroupConfiguration.v2"


class GroupConfigurationListQuerySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Query parameters of a listing of a course's group configurations."""

    view = serializers.ChoiceField(
        choices=[MINIMAL_VIEW, FULL_VIEW],
        default=MINIMAL_VIEW,
        help_text=(
            "How much to return. 'minimal' leaves out of the response where each configuration and group is "
            "used in the course content; 'full' includes it."
        ),
    )

    class Meta:
        ref_name = "GroupConfigurationListQuery.v2"
