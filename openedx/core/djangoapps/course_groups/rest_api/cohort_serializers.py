"""
Serializers for the v2 cohorts REST API.
"""
from django.contrib.auth import get_user_model
from rest_framework import serializers

from openedx.core.djangoapps.course_groups import cohorts

User = get_user_model()

ASSIGNMENT_TYPE_CHOICES = ("manual", "random")


class CohortSerializer(serializers.Serializer):
    """
    Representation of a single cohort within a course.

    ``CourseUserGroup`` carries the name and membership, while the assignment
    type and content-group association live on related models, so the fields
    are declared explicitly rather than derived from one model.
    """

    id = serializers.IntegerField(read_only=True, help_text="Identifier of the cohort.")
    name = serializers.CharField(max_length=255, help_text="Display name of the cohort.")
    assignment_type = serializers.ChoiceField(
        choices=ASSIGNMENT_TYPE_CHOICES,
        help_text="How learners are placed into this cohort.",
    )
    user_count = serializers.IntegerField(
        read_only=True,
        help_text="Number of actively enrolled learners in this cohort.",
    )
    user_partition_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="Identifier of the content group configuration this cohort is associated with.",
    )
    group_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="Identifier of the content group within the configuration. Send null to unlink.",
    )


class CohortUpdateSerializer(CohortSerializer):
    """
    Update payload for a cohort, where every field is optional.

    ``group_id`` is nullable and meaningful when explicitly set to null, which
    unlinks the content group association.
    """

    name = serializers.CharField(max_length=255, required=False)
    assignment_type = serializers.ChoiceField(choices=ASSIGNMENT_TYPE_CHOICES, required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "Supply at least one of name, assignment_type or group_id."
            )
        return attrs


class CohortMemberSerializer(serializers.ModelSerializer):
    """
    Representation of a learner who belongs to a cohort.
    """

    name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("username", "email", "name")

    def get_name(self, user):
        """Return the learner's full name."""
        return f"{user.first_name} {user.last_name}".strip()


class CohortMembershipRequestSerializer(serializers.Serializer):
    """
    Request payload for adding learners to a cohort.
    """

    users = serializers.ListField(
        child=serializers.CharField(allow_blank=False),
        allow_empty=False,
        help_text="Usernames or email addresses to place in this cohort.",
    )


class CohortMembershipResultSerializer(serializers.Serializer):
    """
    Outcome of an add-learners request, partitioned by what happened to each entry.
    """

    added = serializers.ListField(child=serializers.DictField())
    changed = serializers.ListField(child=serializers.DictField())
    present = serializers.ListField(child=serializers.CharField())
    unknown = serializers.ListField(child=serializers.CharField())
    preassigned = serializers.ListField(child=serializers.CharField())
    invalid = serializers.ListField(child=serializers.CharField())


class CohortSettingsSerializer(serializers.Serializer):
    """
    Course-level cohort configuration.
    """

    id = serializers.IntegerField(read_only=True, allow_null=True)
    is_cohorted = serializers.BooleanField(
        help_text="Whether cohorts are enabled for this course.",
    )


def represent_cohort(cohort, course_key):
    """
    Build the serializable representation of a cohort.
    """
    group_id, partition_id = cohorts.get_group_info_for_cohort(cohort)
    return {
        "id": cohort.id,
        "name": cohort.name,
        "assignment_type": cohorts.get_assignment_type(cohort),
        "user_count": cohort.users.filter(
            courseenrollment__course_id=course_key,
            courseenrollment__is_active=1,
        ).count(),
        "user_partition_id": partition_id,
        "group_id": group_id,
    }
